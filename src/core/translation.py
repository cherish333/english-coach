import sqlite3
import datetime
import re
import json
import logging
import asyncio
from typing import Any, Optional, Dict, List
import httpx
from openai import AsyncOpenAI
from src.core.model_runtime import RoutedClient

from src.config import (
    PROJECT_ROOT,
    QWEN_MODEL_NAME,
    TRANSLATION_ENGINE,
    DEEPL_API_KEY,
    DEEPL_API_URL,
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL,
    NVIDIA_TRANSLATE_MODEL,
    NVIDIA_USE_TRANSLATE_MODEL,
)
from src.core.notes_manager import DB_PATH, get_db_connection
from src.core.json_utils import extract_json
from src.core.api_resilience import (
    AsyncRetrier,
    classify_exception,
    classify_response,
    registry,
)

logger = logging.getLogger("english_coach.translation")

def is_valid_chinese_translation(source_text: str, translation: Optional[str]) -> bool:
    """Validate that translation is genuinely Chinese and not an echo of the English source."""
    if not translation or not isinstance(translation, str):
        return False
    t = translation.strip()
    s = source_text.strip()
    if not t or not s:
        return False
    # If translation is identical to source (case-insensitive)
    if t.lower() == s.lower():
        return False
    # If source contains Latin words (at least 2 letters), translation MUST contain Chinese characters
    has_english_words = bool(re.search(r"[a-zA-Z]{2,}", s))
    has_chinese_chars = bool(re.search(r"[\u4e00-\u9fa5]", t))
    if has_english_words and not has_chinese_chars:
        return False
    return True


# Function words are not worth a gloss slot: they inflate the request, and the
# user gets no value from seeing "the -> 这". Filtered in the normalizer so it
# also cleans glosses already sitting in the cache from before this change.
_GLOSS_STOPWORDS = frozenset("""
a an the and or but if of to in on at by for with from into over under about
is am are was were be been being do does did done have has had having will would
shall should can could may might must this that these those it its he she they
them his her their our your my me you we us i as not no nor so than then there
here when where which who whom what why how all any both each few more most
other some such only own same too very just also
""".split())


# Characters that appear in Traditional Chinese but not Simplified. Used to reject
# the occasional Traditional rendering from the translation model so the learner
# always gets Simplified.
TRADITIONAL_ONLY_CHARS = frozenset("圖書無價學語習識與傳統國時間說話點線網路電腦門開關閉")


def _normalize_gloss_dict(parsed: Any) -> Dict[str, str]:
    """Normalize a raw model gloss mapping into ``{word: short chinese gloss}``.

    Keys are reduced to a bare lowercase lookup token and values are trimmed to a
    short sense, so the frontend's word-to-gloss matching keeps working no matter
    how verbose the model was. Shared by the NVIDIA and local gloss paths, which
    previously carried two slightly different copies of this logic.
    """
    if not isinstance(parsed, dict):
        return {}
    cleaned: Dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        k_norm = re.sub(r"^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$", "", key.strip().lower().replace("\u2019", "'"))
        v_first = re.split(r"[/,、;；]", value.strip())[0].strip()
        v_short = v_first[:4].strip()
        v_clean = re.sub(r"[.。,，、/／\\~～…\-_~～\s]+$", "", v_short).strip()
        if k_norm and v_clean and k_norm not in _GLOSS_STOPWORDS:
            cleaned[k_norm] = v_clean
    return cleaned


def clean_translation_output(raw: str) -> str:
    """Remove think tags, codeblocks, common prefixes, markdown formatting, and surrounding quotes."""
    if not raw:
        return ""
    # Strip <think>...</think>
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()

    # Strip markdown codeblocks (including any enclosed block)
    m_code = re.search(r"```(?:[a-zA-Z0-9_\-]+)?\s*([\s\S]*?)\s*```", text)
    if m_code:
        text = m_code.group(1).strip()
    elif text.startswith("```"):
        text = re.sub(r"^```(?:[a-zA-Z0-9_\-]+)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    # Prefix pattern matching labels like 翻译:, 译文:, **翻译**:, **Translation:**, etc.
    prefix_pattern = (
        r"^(?:\*{1,2})?(?:翻译|中文|译文|参考译文|中文翻译|简体中文|Simplified Chinese|Chinese Translation|Translation)"
        r"(?:[：:]\*{1,2}|\*{1,2}[：:]|[：:])\s*"
    )
    text = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip()

    # Strip surrounding quotes and re-check prefixes in case quotes enveloped the prefix or content
    quote_chars = "\"'「『“‘」』”’`"
    for _ in range(3):
        text = text.strip(quote_chars)
        text = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip()

    return text


TRANSLATION_SYSTEM_PROMPT = (
    "You are an expert English-to-Chinese translator for language learners. "
    "Translate the given English text accurately, idiomatically, and naturally into Simplified Chinese (简体中文).\n"
    "Rules:\n"
    "1. Output ONLY the Simplified Chinese translation.\n"
    "2. Never repeat or echo the English text.\n"
    "3. Never include English explanations, notes, pinyin, or quotation marks."
)

BATCH_TRANSLATION_SYSTEM_PROMPT = (
    "You are an expert English-to-Chinese translator for language learners. "
    "Translate each English sentence in the given JSON array into Simplified Chinese (简体中文).\n"
    "Rules:\n"
    "1. Every item in your output MUST be in natural Simplified Chinese.\n"
    "2. Never repeat or echo the English text.\n"
    "3. Respond ONLY with a valid JSON array of Chinese strings matching the exact order and length of the input array.\n"
    "4. Do not include markdown codeblocks, explanations, or commentary.\n"
    "Example:\n"
    'Input: ["Hello.", "How are you?"]\n'
    'Output: ["你好。", "你好吗？"]'
)

WORD_GLOSSES_SYSTEM_PROMPT = (
    "You are an expert English-to-Chinese translator and lexicographer. "
    "For the given English sentence, provide the concise contextual Chinese translation (1-3 Chinese characters) "
    "for each English word according to its exact meaning in this sentence context. "
    "The JSON keys MUST include every word from the sentence in lowercase matching the words as they appear in the sentence. "
    "Respond ONLY with a valid JSON object mapping each lowercase word to its 1-3 character Chinese translation. "
    "Do not include markdown codeblocks, explanation, or commentary. "
    'Example: Input: "Sentence: He runs a company in the city."\n'
    'Output: {"he": "他", "runs": "经营", "a": "一家", "company": "公司", "in": "在", "the": "该", "city": "城市"}'
)



class DeepLHTTPError(Exception):
    """Raised for a non-200 DeepL response so the retrier can classify it."""

    def __init__(self, status_code: int, body: str = "", retry_after: Optional[float] = None):
        super().__init__(f"DeepL HTTP {status_code}: {body[:120]}")
        self.status_code = status_code
        self.body = body
        self.retry_after = retry_after


class TranslationService:
    """Service to translate sentences to Chinese with two-tier caching (memory + SQLite)."""

    def __init__(self):
        self._memory_cache: Dict[str, str] = {}
        self._memory_cache_limit = 5000
        self._glosses_memory_cache: Dict[str, Dict[str, str]] = {}
        self._client: Optional[RoutedClient] = None
        self._nvidia_client: Optional[AsyncOpenAI] = None
        self._http_client: Optional[httpx.AsyncClient] = None

        # Per-provider retry / circuit-breaker / concurrency state. Measured
        # latency for both providers is sub-second, so the retry budget is cheap
        # insurance against the transient stalls that used to drop a sentence's
        # translation permanently.
        self._deepl = registry.register(
            "deepl", failure_threshold=4, cooldown=45.0, concurrency=6
        )
        self._nvidia = registry.register(
            "nvidia", failure_threshold=3, cooldown=60.0, concurrency=4
        )
        # The 4B translation model is a different service with very different
        # latency (sub-2s vs 8-38s), so it gets its own health counters.
        self._riva = registry.register(
            "nvidia_translate", failure_threshold=3, cooldown=45.0, concurrency=6
        )
        self._retrier = AsyncRetrier(max_attempts=3, base_delay=0.4, max_delay=6.0)
        self._init_db()

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            # Connect fast, allow a slower first byte: a stalled TLS handshake
            # should not consume the whole read budget.
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            )
        return self._http_client

    def _get_nvidia_client(self) -> Optional[AsyncOpenAI]:
        if not NVIDIA_API_KEY:
            return None
        if self._nvidia_client is None:
            self._nvidia_client = AsyncOpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=NVIDIA_API_KEY,
                # Our AsyncRetrier owns retrying, so the SDK must not retry too
                # (otherwise attempts multiply and backoff is lost).
                max_retries=0,
                timeout=httpx.Timeout(connect=5.0, read=45.0, write=15.0, pool=5.0),
            )
        return self._nvidia_client

    async def close(self):
        """Cleanly close HTTP and NVIDIA API clients."""
        if self._http_client and not self._http_client.is_closed:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
        if self._nvidia_client:
            try:
                await self._nvidia_client.close()
            except Exception:
                pass
            self._nvidia_client = None

    async def _translate_with_deepl(self, texts: List[str]) -> Optional[List[str]]:
        if not DEEPL_API_KEY or not texts:
            return None
        url = DEEPL_API_URL or ("https://api-free.deepl.com/v2/translate" if DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com/v2/translate")
        headers = {
            "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "text": texts,
            "target_lang": "ZH",
            "source_lang": "EN"
        }
        metrics, breaker, limiter = self._deepl

        async def _call():
            client = self._get_http_client()
            async with limiter():
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                # Raise so the retrier can classify the status and honour
                # Retry-After for 429s instead of silently giving up.
                failure = classify_response(resp.status_code, resp.headers)
                if not failure.retryable:
                    logger.warning(
                        "DeepL rejected the request (HTTP %s, %s): %s",
                        resp.status_code, failure.reason, resp.text[:160],
                    )
                raise DeepLHTTPError(resp.status_code, resp.text[:200], failure.retry_after)
            return resp.json()

        def _validate(data):
            items = data.get("translations", []) if isinstance(data, dict) else []
            if len(items) != len(texts):
                return f"expected {len(texts)} translations, got {len(items)}"
            return None

        data, failure = await self._retrier.run(
            _call,
            provider="DeepL",
            metrics=metrics,
            breaker=breaker,
            validate=_validate,
        )
        if data is None:
            logger.warning("DeepL translation unavailable (%s)", failure.reason if failure else "unknown")
            return None
        translations = [clean_translation_output(item.get("text", "")) for item in data.get("translations", [])]
        if len(translations) == len(texts):
            return translations
        return None

    async def _translate_sentences_with_riva(
        self, texts: List[str]
    ) -> Optional[List[str]]:
        """Translate sentences with the dedicated 4B translation model.

        Measured 0.6-1.9s per sentence versus 8-17s for the general instruct
        model, so this is the preferred NVIDIA path. It is a pure translation
        model: it needs a plain "translate to X" prompt and cannot produce
        structured JSON, which is why glosses and whiteboards still use the
        general model.

        DeepL remains first choice for sentences because its phrasing measured
        more natural ("关键不在于钉子，而在于孔" vs "这不是关于钉子,而是关于孔"),
        but this is far faster than the general model when DeepL is unavailable
        (quota exhausted, outage, or offline).
        """
        client = self._get_nvidia_client()
        if not client or not texts or not NVIDIA_USE_TRANSLATE_MODEL:
            return None
        metrics, breaker, limiter = self._riva

        async def _call():
            async with limiter():
                # CRITICAL: this model ignores the system message. With the target
                # language only in the system role it either echoes the English back
                # or auto-detects a random target language — observed outputs
                # included unchanged English, Norwegian ("Den antikke biblioteket
                # inneholder...") and Czech. The "Translate to Simplified Chinese:"
                # prefix must live in the USER message.
                prefix = "Translate to Simplified Chinese: "
                payload = texts[0] if len(texts) == 1 else "\n".join(texts)
                return await client.chat.completions.create(
                    model=NVIDIA_TRANSLATE_MODEL,
                    messages=[{"role": "user", "content": f"{prefix}{payload}"}],
                    temperature=0.1,
                    max_tokens=max(120, 160 * len(texts)),
                )

        def _validate(resp) -> Optional[str]:
            raw = (resp.choices[0].message.content or "").strip()
            if not raw:
                return "empty_output"
            # A pure translation model must answer in Chinese; anything else means
            # it echoed the source or auto-detected the wrong language.
            if not re.search(r"[\u4e00-\u9fff]", raw):
                return "no_chinese_in_output"
            # It sometimes produces Traditional Chinese ("古老的圖書館藏有無價的手稿").
            # Retry so the learner gets Simplified consistently.
            if TRADITIONAL_ONLY_CHARS & set(raw):
                return "traditional_chinese"
            return None

        resp, failure = await self._retrier.run(
            _call, provider="NVIDIA-Translate", metrics=metrics, breaker=breaker, validate=_validate
        )
        if resp is None:
            logger.info(
                "NVIDIA translation model unavailable (%s)",
                failure.reason if failure else "unknown",
            )
            return None

        raw = clean_translation_output((resp.choices[0].message.content or "").strip())
        if len(texts) == 1:
            return [raw] if is_valid_chinese_translation(texts[0], raw) else None
        # Multi-line output: keep it only when the line count matches.
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if len(lines) != len(texts):
            return None
        cleaned = [clean_translation_output(ln) for ln in lines]
        return cleaned if all(
            is_valid_chinese_translation(src, out) for src, out in zip(texts, cleaned)
        ) else None

    async def _translate_with_nvidia(self, texts: List[str]) -> Optional[List[str]]:
        # Preferred NVIDIA path: the dedicated 4B translation model (0.6-1.9s vs
        # 8-17s). Only fall through to the general instruct model if it fails, so
        # the slow path is used for quality/robustness rather than by default.
        riva_result = await self._translate_sentences_with_riva(texts)
        if riva_result is not None:
            return riva_result

        client = self._get_nvidia_client()
        if not client or not texts:
            return None
        metrics, breaker, limiter = self._nvidia

        if len(texts) == 1:
            async def _call_single():
                async with limiter():
                    return await client.chat.completions.create(
                        model=NVIDIA_MODEL,
                        messages=[
                            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                            {"role": "user", "content": f"Translate to Chinese: {texts[0]}"}
                        ],
                        temperature=0.1,
                        max_tokens=250,
                    )

            def _validate_single(resp):
                cleaned = clean_translation_output((resp.choices[0].message.content or "").strip())
                if not is_valid_chinese_translation(texts[0], cleaned):
                    return "invalid_chinese_translation"
                return None

            resp, failure = await self._retrier.run(
                _call_single, provider="NVIDIA", metrics=metrics, breaker=breaker,
                validate=_validate_single,
            )
            if resp is None:
                logger.warning(
                    "NVIDIA translation failed (%s)", failure.reason if failure else "unknown"
                )
                return None
            cleaned = clean_translation_output((resp.choices[0].message.content or "").strip())
            return [cleaned] if is_valid_chinese_translation(texts[0], cleaned) else None

        prompt = (
            "Translate these English sentences to Simplified Chinese JSON array:\n"
            f"{json.dumps(texts, ensure_ascii=False)}"
        )
        parsed_holder: Dict[str, Any] = {}

        async def _call_batch():
            async with limiter():
                resp = await client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=[
                        {"role": "system", "content": BATCH_TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1400,
                )
            raw = clean_translation_output((resp.choices[0].message.content or "").strip())
            # Balanced-brace extraction: a greedy regex swallowed any bracketed
            # text after the payload and forced a slow per-item fallback even
            # when the model had answered correctly.
            parsed, _ = extract_json(raw)
            if parsed is None:
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = None
            parsed_holder["value"] = parsed
            return parsed

        def _validate_batch(parsed):
            if not isinstance(parsed, list):
                return "not_a_json_array"
            if len(parsed) != len(texts):
                return f"expected {len(texts)} items, got {len(parsed)}"
            return None

        result, failure = await self._retrier.run(
            _call_batch, provider="NVIDIA", metrics=metrics, breaker=breaker,
            validate=_validate_batch,
        )
        if result is None:
            logger.warning(
                "NVIDIA batch translation failed (%s)", failure.reason if failure else "unknown"
            )
            return None
        return [clean_translation_output(str(t) if t else "") for t in result]

    async def _get_glosses_with_nvidia(self, sentence: str) -> Optional[Dict[str, str]]:
        client = self._get_nvidia_client()
        if not client or not sentence:
            return None
        metrics, breaker, limiter = self._nvidia

        async def _call():
            async with limiter():
                return await client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=[
                        {"role": "system", "content": WORD_GLOSSES_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Sentence: {sentence}"}
                    ],
                    temperature=0.1,
                    max_tokens=800,
                )

        def _validate(resp) -> Optional[str]:
            # A response without a usable JSON object is retried: the model
            # occasionally emits prose instead, and glosses are the whole point
            # of this call.
            text = resp.choices[0].message.content or ""
            parsed, _ = extract_json(text)
            if isinstance(parsed, dict) and _normalize_gloss_dict(parsed):
                return None
            return "no_usable_gloss_json"

        try:
            resp, failure = await self._retrier.run(
                _call, provider="NVIDIA", metrics=metrics, breaker=breaker, validate=_validate
            )
            if resp is None:
                logger.warning(
                    "NVIDIA word glosses failed (%s)", failure.reason if failure else "unknown"
                )
                return None
            content = resp.choices[0].message.content or ""
            parsed, raw = extract_json(content)
            if parsed is not None:
                return _normalize_gloss_dict(parsed)
            content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
            match = re.search(r"\{[\s\S]*\}", content)
            raw = match.group(0) if match else content.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                try:
                    repaired = re.sub(r",\s*([}\]])", r"\1", raw)
                    if not repaired.endswith("}"):
                        repaired = repaired.rsplit(",", 1)[0] + "}"
                    parsed = json.loads(repaired)
                except Exception:
                    pass

            if isinstance(parsed, dict) and parsed:
                cleaned = _normalize_gloss_dict(parsed)
                if cleaned:
                    return cleaned
        except Exception as exc:
            logger.warning(f"NVIDIA NIM word glosses failed: {exc}")
        return None

    def get_status(self) -> dict:
        return {
            "engine_preference": TRANSLATION_ENGINE,
            "active_sentence_engine": (
                "DeepL API" if (TRANSLATION_ENGINE in ("auto", "deepl") and DEEPL_API_KEY)
                else "NVIDIA NIM" if (TRANSLATION_ENGINE in ("auto", "nvidia") and NVIDIA_API_KEY)
                else "Local Model"
            ),
            "active_glosses_engine": (
                "NVIDIA NIM" if (TRANSLATION_ENGINE in ("auto", "nvidia") and NVIDIA_API_KEY)
                else "Local Model"
            ),
            "active_smart_notes_engine": (
                "NVIDIA NIM" if NVIDIA_API_KEY else "Local Model"
            ),
            "deepl_configured": bool(DEEPL_API_KEY),
            "nvidia_configured": bool(NVIDIA_API_KEY),
            "deepl_api_url": "free" if "api-free" in (DEEPL_API_URL or "") else "pro",
            "nvidia_model": NVIDIA_MODEL,
            "memory_cached_sentences": len(self._memory_cache),
            "memory_cached_glosses": len(self._glosses_memory_cache),
            # Live provider health: attempts, success rate, p50 latency, retries
            # and circuit state. Without this the user could only guess whether a
            # cloud engine was working.
            "providers": registry.snapshot(),
            "retry_policy": {
                "max_attempts": self._retrier.max_attempts,
                "base_delay_seconds": self._retrier.base_delay,
                "max_delay_seconds": self._retrier.max_delay,
            },
        }

    def _remember(self, source_text: str, translation: str) -> None:
        """Insert into the memory cache with a hard size bound.

        Both the read and write paths previously inlined their own eviction, and
        a plain dict gives insertion-order eviction — so a frequently reused
        translation could be dropped while a one-off stayed resident.
        """
        self._memory_cache[source_text] = translation
        while len(self._memory_cache) > self._memory_cache_limit:
            oldest = next(iter(self._memory_cache))
            self._memory_cache.pop(oldest, None)

    def _remember_glosses(self, sentence: str, glosses: Dict[str, str]) -> None:
        """Bounded insert for the per-sentence gloss cache."""
        self._glosses_memory_cache[sentence] = glosses
        while len(self._glosses_memory_cache) > self._memory_cache_limit:
            oldest = next(iter(self._glosses_memory_cache))
            self._glosses_memory_cache.pop(oldest, None)

    def _get_client(self) -> RoutedClient:
        if self._client is None:
            self._client = RoutedClient()
        return self._client

    def _get_conn(self) -> sqlite3.Connection:
        return get_db_connection()

    def _init_db(self):
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sentence_translations (
                        source_text TEXT PRIMARY KEY,
                        target_lang TEXT NOT NULL DEFAULT 'zh',
                        translation TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_trans_lang ON sentence_translations(target_lang)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sentence_word_glosses (
                        source_text TEXT PRIMARY KEY,
                        glosses_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                # Purge poisoned cache entries where translation equals source or lacks Chinese for English words
                cursor = conn.execute("SELECT source_text, translation FROM sentence_translations WHERE target_lang = 'zh'")
                rows = cursor.fetchall()
                bad_keys = [
                    row["source_text"] for row in rows
                    if not is_valid_chinese_translation(row["source_text"], row["translation"])
                ]
                if bad_keys:
                    conn.executemany(
                        "DELETE FROM sentence_translations WHERE source_text = ? AND target_lang = 'zh'",
                        [(k,) for k in bad_keys]
                    )
                    logger.info(f"Purged {len(bad_keys)} invalid/poisoned translation entries from database.")
        except Exception as exc:
            logger.warning(f"Failed to initialize sentence_translations table: {exc}")
        finally:
            conn.close()

    def get_cached(self, text: str) -> Optional[str]:
        cleaned = text.strip()
        if not cleaned:
            return ""
        if cleaned in self._memory_cache:
            val = self._memory_cache[cleaned]
            if is_valid_chinese_translation(cleaned, val):
                return val
            self._memory_cache.pop(cleaned, None)

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT translation FROM sentence_translations WHERE source_text = ? AND target_lang = 'zh'",
                (cleaned,)
            )
            row = cursor.fetchone()
            if row:
                trans = row["translation"]
                if is_valid_chinese_translation(cleaned, trans):
                    self._remember(cleaned, trans)
                    return trans
                else:
                    # Invalidate and delete bad row from DB
                    with conn:
                        conn.execute("DELETE FROM sentence_translations WHERE source_text = ? AND target_lang = 'zh'", (cleaned,))
        except Exception as exc:
            logger.warning(f"Error querying translation cache: {exc}")
        finally:
            conn.close()

        return None

    def save_cache(self, text: str, translation: str):
        cleaned = text.strip()
        cleaned_trans = clean_translation_output(translation.strip())
        if not is_valid_chinese_translation(cleaned, cleaned_trans):
            return

        self._remember(cleaned, cleaned_trans)
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sentence_translations (source_text, target_lang, translation, created_at)
                    VALUES (?, 'zh', ?, ?)
                    ON CONFLICT(source_text) DO UPDATE SET
                        translation = excluded.translation,
                        created_at = excluded.created_at
                    """,
                    (cleaned, cleaned_trans, datetime.datetime.now(datetime.timezone.utc).isoformat())
                )
        except Exception as exc:
            logger.warning(f"Error persisting translation to database: {exc}")
        finally:
            conn.close()

    async def translate_sentence(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return ""

        cached = self.get_cached(cleaned)
        if cached:
            return cached

        # 1. Try DeepL API if configured
        if TRANSLATION_ENGINE in ("auto", "deepl") and DEEPL_API_KEY:
            deepl_res = await self._translate_with_deepl([cleaned])
            if deepl_res and len(deepl_res) == 1:
                cand = deepl_res[0]
                if is_valid_chinese_translation(cleaned, cand):
                    self.save_cache(cleaned, cand)
                    return cand
            logger.info(f"DeepL translation unavailable or failed for '{cleaned[:30]}...', attempting fallback...")

        # 2. Try NVIDIA NIM API if configured
        if TRANSLATION_ENGINE in ("auto", "nvidia") and NVIDIA_API_KEY:
            nv_res = await self._translate_with_nvidia([cleaned])
            if nv_res and len(nv_res) == 1:
                cand = nv_res[0]
                if is_valid_chinese_translation(cleaned, cand):
                    self.save_cache(cleaned, cand)
                    return cand
            logger.info(f"NVIDIA NIM translation unavailable or failed for '{cleaned[:30]}...', attempting local fallback...")

        # 2. Fallback: Local RoutedClient (Qwen / Bonsai)
        client = self._get_client()
        try:
            resp = await client.chat.completions.create(
                model=QWEN_MODEL_NAME,
                messages=[
                    {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Translate to Chinese: {cleaned}"}
                ],
                temperature=0.1,
                max_tokens=200,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            )
            choice = resp.choices[0]
            raw = choice.message.content or ""
            result = clean_translation_output(raw)
            if is_valid_chinese_translation(cleaned, result):
                self.save_cache(cleaned, result)
                return result

            # Retry once with explicit Chinese characters instruction if validation failed
            logger.info(f"First local translation validation failed for '{cleaned[:30]}', retrying with explicit directive...")
            resp_retry = await client.chat.completions.create(
                model=QWEN_MODEL_NAME,
                messages=[
                    {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                    {"role": "user", "content": f"请将下面的英文句子准确翻译成简体中文（必须输出中文汉字，严禁重复英文）：\n{cleaned}"}
                ],
                temperature=0.1,
                max_tokens=200,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            )
            raw_retry = resp_retry.choices[0].message.content or ""
            result_retry = clean_translation_output(raw_retry)
            if is_valid_chinese_translation(cleaned, result_retry):
                self.save_cache(cleaned, result_retry)
                return result_retry
        except Exception as exc:
            logger.warning(f"Translation call failed for '{cleaned[:30]}...': {exc}")

        return ""

    async def translate_batch(self, texts: List[str]) -> Dict[str, str]:
        results: Dict[str, str] = {}
        missing_texts: List[str] = []

        for t in texts:
            cleaned = t.strip()
            if not cleaned:
                continue
            cached = self.get_cached(cleaned)
            if cached:
                results[cleaned] = cached
            else:
                missing_texts.append(cleaned)

        if not missing_texts:
            return results

        # 1. Try DeepL API for missing texts (batch chunks up to 40)
        if TRANSLATION_ENGINE in ("auto", "deepl") and DEEPL_API_KEY:
            unresolved = []
            chunk_size = 40
            for i in range(0, len(missing_texts), chunk_size):
                chunk = missing_texts[i:i + chunk_size]
                deepl_res = await self._translate_with_deepl(chunk)
                if deepl_res and len(deepl_res) == len(chunk):
                    for src, trans in zip(chunk, deepl_res):
                        if is_valid_chinese_translation(src, trans):
                            self.save_cache(src, trans)
                            results[src] = trans
                        else:
                            unresolved.append(src)
                else:
                    unresolved.extend(chunk)
            missing_texts = unresolved

        if not missing_texts:
            return results

        # 2. Try NVIDIA NIM API for remaining missing texts (chunks up to 10)
        if TRANSLATION_ENGINE in ("auto", "nvidia") and NVIDIA_API_KEY:
            unresolved = []
            chunk_size = 10
            for i in range(0, len(missing_texts), chunk_size):
                chunk = missing_texts[i:i + chunk_size]
                nv_res = await self._translate_with_nvidia(chunk)
                if nv_res and len(nv_res) == len(chunk):
                    for src, trans in zip(chunk, nv_res):
                        if is_valid_chinese_translation(src, trans):
                            self.save_cache(src, trans)
                            results[src] = trans
                        else:
                            unresolved.append(src)
                else:
                    unresolved.extend(chunk)
            missing_texts = unresolved

        if not missing_texts:
            return results

        # 3. Fallback: Local RoutedClient
        if len(missing_texts) <= 2:
            tasks = [self.translate_sentence(t) for t in missing_texts]
            translated = await asyncio.gather(*tasks, return_exceptions=True)
            for t, res in zip(missing_texts, translated):
                if isinstance(res, str) and res:
                    results[t] = res
            return results

        # Batch request to local LLM (up to 10 items at once)
        chunk_size = 10
        for i in range(0, len(missing_texts), chunk_size):
            chunk = missing_texts[i:i + chunk_size]
            client = self._get_client()
            failed_chunk_items = []
            try:
                prompt = f"Translate these English sentences to Simplified Chinese JSON array:\n{json.dumps(chunk, ensure_ascii=False)}"
                resp = await client.chat.completions.create(
                    model=QWEN_MODEL_NAME,
                    messages=[
                        {"role": "system", "content": BATCH_TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=700,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}}
                )
                raw = (resp.choices[0].message.content or "").strip()
                raw = clean_translation_output(raw)
                parsed, _ = extract_json(raw)
                if parsed is None:
                    parsed = json.loads(raw)
                if isinstance(parsed, list) and len(parsed) == len(chunk):
                    for src, trans in zip(chunk, parsed):
                        clean_t = clean_translation_output(str(trans) if trans else "")
                        if is_valid_chinese_translation(src, clean_t):
                            self.save_cache(src, clean_t)
                            results[src] = clean_t
                        else:
                            failed_chunk_items.append(src)
                else:
                    failed_chunk_items = chunk
            except Exception as exc:
                logger.warning(f"Batch translation chunk failed: {exc}, falling back to individual")
                failed_chunk_items = chunk

            if failed_chunk_items:
                tasks = [self.translate_sentence(t) for t in failed_chunk_items]
                translated = await asyncio.gather(*tasks, return_exceptions=True)
                for src, res in zip(failed_chunk_items, translated):
                    if isinstance(res, str) and res:
                        results[src] = res

        return results

    def get_cached_glosses(self, text: str) -> Optional[Dict[str, str]]:
        cleaned = text.strip()
        if not cleaned:
            return {}
        if cleaned in self._glosses_memory_cache:
            return self._glosses_memory_cache[cleaned]

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT glosses_json FROM sentence_word_glosses WHERE source_text = ?",
                (cleaned,)
            )
            row = cursor.fetchone()
            if row:
                glosses = json.loads(row["glosses_json"])
                if isinstance(glosses, dict):
                    self._remember_glosses(cleaned, glosses)
                    return glosses
        except Exception as exc:
            logger.warning(f"Error querying glosses cache: {exc}")
        finally:
            conn.close()

        return None

    def save_glosses_cache(self, text: str, glosses: Dict[str, str]):
        cleaned = text.strip()
        if not cleaned or not glosses:
            return

        self._remember_glosses(cleaned, glosses)
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sentence_word_glosses (source_text, glosses_json, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(source_text) DO UPDATE SET
                        glosses_json = excluded.glosses_json,
                        created_at = excluded.created_at
                    """,
                    (cleaned, json.dumps(glosses, ensure_ascii=False), datetime.datetime.now(datetime.timezone.utc).isoformat())
                )
        except Exception as exc:
            logger.warning(f"Error persisting glosses to database: {exc}")
        finally:
            conn.close()

    async def get_sentence_word_glosses(self, sentence: str) -> Dict[str, str]:
        cleaned = re.sub(r"[\u00a0\u202f\u2009\u3000]", " ", sentence).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            return {}

        cached = self.get_cached_glosses(cleaned)
        if cached is not None:
            return cached

        # 1. Structured glosses from the general instruct model.
        #    NOTE: the dedicated translation model (riva-translate) is deliberately
        #    NOT used here. It is a pure translation model, so asking it for a JSON
        #    word-to-gloss dictionary made it auto-detect the language and answer in
        #    Czech / Arabic (observed: "Klíčová slova: učení, angličtina..."). Only a
        #    general instruct model honours the JSON contract.
        if TRANSLATION_ENGINE in ("auto", "nvidia") and NVIDIA_API_KEY:
            nv_glosses = await self._get_glosses_with_nvidia(cleaned)
            if nv_glosses:
                self.save_glosses_cache(cleaned, nv_glosses)
                return nv_glosses
            logger.info(f"NVIDIA NIM glosses unavailable for '{cleaned[:30]}...', attempting local fallback...")

        # 3. Fallback: Local RoutedClient (Qwen / Bonsai)
        client = self._get_client()
        try:
            resp = await client.chat.completions.create(
                model=QWEN_MODEL_NAME,
                messages=[
                    {"role": "system", "content": WORD_GLOSSES_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Sentence: {cleaned}"}
                ],
                temperature=0.1,
                max_tokens=800,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            )
            content = resp.choices[0].message.content or ""
            parsed, raw = extract_json(content)
            if parsed is not None:
                return _normalize_gloss_dict(parsed)
            content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
            match = re.search(r"\{[\s\S]*\}", content)
            raw = match.group(0) if match else content.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                # Handle possible trailing commas or mild truncation
                try:
                    repaired = re.sub(r",\s*([}\]])", r"\1", raw)
                    if not repaired.endswith("}"):
                        repaired = repaired.rsplit(",", 1)[0] + "}"
                    parsed = json.loads(repaired)
                except Exception:
                    pass

            if isinstance(parsed, dict):
                cleaned_dict = {}
                for k, v in parsed.items():
                    if isinstance(k, str) and isinstance(v, str):
                        k_norm = re.sub(r"^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$", "", k.strip().lower().replace("’", "'"))
                        v_first = re.split(r"[/,、;；]", v.strip())[0].strip()
                        v_short = v_first[:4].strip()
                        v_clean = re.sub(r"[.。,，、/／\\~～…\-_~～\s]+$", "", v_short).strip()
                        if k_norm and v_clean:
                            cleaned_dict[k_norm] = v_clean
                if cleaned_dict:
                    self.save_glosses_cache(cleaned, cleaned_dict)
                    return cleaned_dict
        except Exception as exc:
            logger.warning(f"Failed to generate word glosses for '{cleaned[:30]}...': {exc}")

        return {}


# Global singleton instance
translation_service = TranslationService()
