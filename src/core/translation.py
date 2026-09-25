import sqlite3
import datetime
import re
import json
import logging
import asyncio
from typing import Optional, Dict, List
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
)
from src.core.notes_manager import DB_PATH, get_db_connection

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



class TranslationService:
    """Service to translate sentences to Chinese with two-tier caching (memory + SQLite)."""

    def __init__(self):
        self._memory_cache: Dict[str, str] = {}
        self._glosses_memory_cache: Dict[str, Dict[str, str]] = {}
        self._client: Optional[RoutedClient] = None
        self._nvidia_client: Optional[AsyncOpenAI] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._init_db()

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    def _get_nvidia_client(self) -> Optional[AsyncOpenAI]:
        if not NVIDIA_API_KEY:
            return None
        if self._nvidia_client is None:
            self._nvidia_client = AsyncOpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=NVIDIA_API_KEY,
                timeout=15.0
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
        try:
            client = self._get_http_client()
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                raw_translations = data.get("translations", [])
                translations = [clean_translation_output(item.get("text", "")) for item in raw_translations]
                if len(translations) == len(texts):
                    return translations
            else:
                logger.warning(f"DeepL API HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            logger.warning(f"DeepL API request failed: {exc}")
        return None

    async def _translate_with_nvidia(self, texts: List[str]) -> Optional[List[str]]:
        client = self._get_nvidia_client()
        if not client or not texts:
            return None
        if len(texts) == 1:
            try:
                resp = await client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=[
                        {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Translate to Chinese: {texts[0]}"}
                    ],
                    temperature=0.1,
                    max_tokens=250,
                )
                raw = (resp.choices[0].message.content or "").strip()
                cleaned = clean_translation_output(raw)
                if is_valid_chinese_translation(texts[0], cleaned):
                    return [cleaned]
            except Exception as exc:
                logger.warning(f"NVIDIA NIM single translation failed: {exc}")
            return None
        else:
            try:
                prompt = f"Translate these English sentences to Simplified Chinese JSON array:\n{json.dumps(texts, ensure_ascii=False)}"
                resp = await client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=[
                        {"role": "system", "content": BATCH_TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1000,
                )
                raw = (resp.choices[0].message.content or "").strip()
                raw = clean_translation_output(raw)
                match = re.search(r"\[[\s\S]*\]", raw)
                raw_json = match.group(0) if match else raw
                parsed = json.loads(raw_json)
                if isinstance(parsed, list) and len(parsed) == len(texts):
                    cleaned_items = [clean_translation_output(str(t) if t else "") for t in parsed]
                    return cleaned_items
            except Exception as exc:
                logger.warning(f"NVIDIA NIM batch translation failed: {exc}")
            return None

    async def _get_glosses_with_nvidia(self, sentence: str) -> Optional[Dict[str, str]]:
        client = self._get_nvidia_client()
        if not client or not sentence:
            return None
        try:
            resp = await client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[
                    {"role": "system", "content": WORD_GLOSSES_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Sentence: {sentence}"}
                ],
                temperature=0.1,
                max_tokens=800,
            )
            content = resp.choices[0].message.content or ""
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
                    return cleaned_dict
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
            "memory_cached_sentences": len(self._memory_cache),
            "memory_cached_glosses": len(self._glosses_memory_cache),
        }

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
                    self._memory_cache[cleaned] = trans
                    if len(self._memory_cache) > 5000:
                        first_key = next(iter(self._memory_cache))
                        self._memory_cache.pop(first_key, None)
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

        self._memory_cache[cleaned] = cleaned_trans
        if len(self._memory_cache) > 5000:
            first_key = next(iter(self._memory_cache))
            self._memory_cache.pop(first_key, None)
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

        # 3. Fallback: Local RoutedClient (Qwen / Bonsai)
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
                match = re.search(r"\[[\s\S]*\]", raw)
                raw_json = match.group(0) if match else raw

                parsed = json.loads(raw_json)
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
                    self._glosses_memory_cache[cleaned] = glosses
                    if len(self._glosses_memory_cache) > 5000:
                        first_key = next(iter(self._glosses_memory_cache))
                        self._glosses_memory_cache.pop(first_key, None)
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

        self._glosses_memory_cache[cleaned] = glosses
        if len(self._glosses_memory_cache) > 5000:
            first_key = next(iter(self._glosses_memory_cache))
            self._glosses_memory_cache.pop(first_key, None)
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

        # 1. Try NVIDIA NIM API if configured
        if TRANSLATION_ENGINE in ("auto", "nvidia") and NVIDIA_API_KEY:
            nv_glosses = await self._get_glosses_with_nvidia(cleaned)
            if nv_glosses:
                self.save_glosses_cache(cleaned, nv_glosses)
                return nv_glosses
            logger.info(f"NVIDIA NIM glosses unavailable for '{cleaned[:30]}...', attempting local fallback...")

        # 2. Fallback: Local RoutedClient (Qwen / Bonsai)
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
