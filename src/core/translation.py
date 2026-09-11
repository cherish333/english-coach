import sqlite3
import datetime
import re
import json
import logging
import asyncio
from typing import Optional, Dict, List
from src.core.model_runtime import RoutedClient

from src.config import PROJECT_ROOT, QWEN_MODEL_NAME
from src.core.notes_manager import DB_PATH, get_db_connection

logger = logging.getLogger("english_coach.translation")

TRANSLATION_SYSTEM_PROMPT = (
    "You are an expert English-to-Chinese translator. "
    "Translate the English text accurately, idiomatically, and naturally into Simplified Chinese. "
    "Output ONLY the Chinese translation. Never output English text, explanations, notes, or quotation marks."
)

BATCH_TRANSLATION_SYSTEM_PROMPT = (
    "You are an expert English-to-Chinese translator. "
    "Translate the following JSON array of English sentences into Simplified Chinese. "
    "Respond ONLY with a valid JSON array of Chinese strings matching the exact order of the input array. "
    "Do not include markdown codeblocks, explanation, or commentary."
)

WORD_GLOSSES_SYSTEM_PROMPT = (
    "You are an expert English-to-Chinese translator and lexicographer. "
    "For the given English sentence, provide the concise contextual Chinese translation (1-3 Chinese characters) "
    "for each English word according to its exact meaning in this sentence context. "
    "The JSON keys MUST include every word from the sentence in lowercase matching the words as they appear in the sentence. "
    "Respond ONLY with a valid JSON object mapping each lowercase word to its 1-3 character Chinese translation. "
    "Do not include markdown codeblocks, explanation, or commentary. "
    "Example: Input: \"Sentence: He runs a company in the city.\"\n"
    "Output: {\"he\": \"他\", \"runs\": \"经营\", \"a\": \"一家\", \"company\": \"公司\", \"in\": \"在\", \"the\": \"该\", \"city\": \"城市\"}"
)



class TranslationService:
    """Service to translate sentences to Chinese with two-tier caching (memory + SQLite)."""

    def __init__(self):
        self._memory_cache: Dict[str, str] = {}
        self._glosses_memory_cache: Dict[str, Dict[str, str]] = {}
        self._client: Optional[RoutedClient] = None
        self._init_db()

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
        except Exception as exc:
            logger.warning(f"Failed to initialize sentence_translations table: {exc}")
        finally:
            conn.close()

    def get_cached(self, text: str) -> Optional[str]:
        cleaned = text.strip()
        if not cleaned:
            return ""
        if cleaned in self._memory_cache:
            return self._memory_cache[cleaned]

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT translation FROM sentence_translations WHERE source_text = ? AND target_lang = 'zh'",
                (cleaned,)
            )
            row = cursor.fetchone()
            if row:
                trans = row["translation"]
                self._memory_cache[cleaned] = trans
                if len(self._memory_cache) > 5000:
                    first_key = next(iter(self._memory_cache))
                    self._memory_cache.pop(first_key, None)
                return trans
        except Exception as exc:
            logger.warning(f"Error querying translation cache: {exc}")
        finally:
            conn.close()

        return None

    def save_cache(self, text: str, translation: str):
        cleaned = text.strip()
        cleaned_trans = translation.strip()
        if not cleaned or not cleaned_trans:
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

        client = self._get_client()
        try:
            resp = await client.chat.completions.create(
                model=QWEN_MODEL_NAME,
                messages=[
                    {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                    {"role": "user", "content": cleaned}
                ],
                temperature=0.1,
                max_tokens=150,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            )
            choice = resp.choices[0]
            raw = (choice.message.content or "").strip()
            # Clean up potential quotation marks or markdown
            result = re.sub(r'^["\'「『“]|["\'」』”]$', '', raw).strip()
            if result:
                self.save_cache(cleaned, result)
                return result
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

        # If 1 or 2 missing, translate individually concurrently
        if len(missing_texts) <= 2:
            tasks = [self.translate_sentence(t) for t in missing_texts]
            translated = await asyncio.gather(*tasks, return_exceptions=True)
            for t, res in zip(missing_texts, translated):
                if isinstance(res, str) and res:
                    results[t] = res
            return results

        # Batch request to LLM (up to 12 items at once)
        chunk_size = 10
        for i in range(0, len(missing_texts), chunk_size):
            chunk = missing_texts[i:i + chunk_size]
            client = self._get_client()
            try:
                prompt = json.dumps(chunk, ensure_ascii=False)
                resp = await client.chat.completions.create(
                    model=QWEN_MODEL_NAME,
                    messages=[
                        {"role": "system", "content": BATCH_TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=600,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}}
                )
                raw = (resp.choices[0].message.content or "").strip()
                # Remove code blocks if present
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\s*", "", raw)
                    raw = re.sub(r"\s*```$", "", raw)
                parsed = json.loads(raw)
                if isinstance(parsed, list) and len(parsed) == len(chunk):
                    for src, trans in zip(chunk, parsed):
                        if isinstance(trans, str) and trans.strip():
                            t_clean = trans.strip()
                            self.save_cache(src, t_clean)
                            results[src] = t_clean
                else:
                    # Fallback to individual
                    tasks = [self.translate_sentence(t) for t in chunk]
                    translated = await asyncio.gather(*tasks, return_exceptions=True)
                    for src, res in zip(chunk, translated):
                        if isinstance(res, str) and res:
                            results[src] = res
            except Exception as exc:
                logger.warning(f"Batch translation chunk failed: {exc}, falling back to individual")
                tasks = [self.translate_sentence(t) for t in chunk]
                translated = await asyncio.gather(*tasks, return_exceptions=True)
                for src, res in zip(chunk, translated):
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
