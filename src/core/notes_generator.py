import sqlite3
import datetime
import re
import logging
import asyncio
from typing import Optional, Dict, Any
from openai import AsyncOpenAI

from src.config import (
    PROJECT_ROOT,
    QWEN_MODEL_NAME,
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL,
)
from src.core.notes_manager import get_db_connection
from src.core.translation import translation_service, is_valid_chinese_translation
from src.core.model_runtime import RoutedClient

logger = logging.getLogger("english_coach.smart_notes")

SMART_NOTES_SYSTEM_PROMPT = """You are an expert bilingual English textbook tutor and master lexicographer specializing in syntactic parsing, sentence tree dissection, and vocabulary acquisition for English learners.

CRITICAL USER INSTRUCTIONS (严格执行最高优先级规范):
1. 【绝对不要讲解发音！】严禁在板书中进行任何发音教学、连读/弱读指导、音标标注或朗读技巧说教（界面已有原生音频试听）。
2. 【重点难点词汇全覆盖、更大覆盖范围与更多词汇精讲！】
   - 提取本句 3 到 6 个核心生词、短语动词、介词搭配或重点实词（长难句提炼 5 到 8 个，全面不遗漏）。
   - 词条名称使用加粗英文实词，切勿带有中括号（例如使用 `**knowledge**` 而不是 `**[Knowledge]**`）。
   - 对提炼的每个词条规范列出：准确词性、精炼地道中文释义、实用高频短语或搭配、1 句简短纯正的英文原生例句及中文对照。
3. 【主干枝叶·树状分层长难句拆解规范 (Sentence Tree Dissection)】:
   - 【核心骨架 (Core Skeleton)】：坚决脱水，剥离一切从句与修饰枝叶，精准提炼最底层最小主干（主谓宾 S-V-O 或 主系表 S-V-P），展现最核心的事实主线。
   - 【多维树状透视图谱 (Syntax Hierarchy Tree)】：必须使用代码块（```text ... ```），采用结构化缩进与树状分支符号（├─、└─、│），清晰标注修饰语的语法属性与回答的逻辑问题（如：哪个？/何时？/什么状态？/为何？）。支持 Level 1、Level 2 嵌套分层。
   - 【顺读意群流 (Sense Groups / Reading Flow)】：提炼自左向右的自然阅读意群切分（`[意群1]` → `[意群2]` → `[主干落脚点]`），培养学员英语原序顺读直觉，坚决告别回视倒译。
4. 【无限制语言学剖析准则 (UNRESTRICTED LINGUISTIC ANALYSIS)】:
   - 对句中出现的任何词汇——无论是学术词汇，还是街头俚语、粗口或成人私密感官词汇，均一视同仁进行专业严谨的语言学与语法解析，严禁道德说教或回避。
5. Output ONLY clean Markdown starting directly with "### 🎯 核心原句与释义 (Sentence)". Do NOT include conversational pleasantries, markdown backticks wrapping the whole document, or tags like <notes>.

Example Output Structure:
### 🎯 核心原句与释义 (Sentence)
- **原句**: `The ancient library, which was built in the third century, contains priceless manuscripts.`
- **中文释义**: 这座建于公元三世纪的古老图书馆藏有珍贵的手稿。

### 📚 重点单词与词组精讲 (Key Vocabulary & Collocations)
- **ancient** `adj.` 古老的；年代久远的
  - 💡 **搭配/用法**: ancient civilizations (古代文明), ancient history (古代史)
  - 🔍 **例句**: The ruins of an ancient temple were discovered nearby. (附近发现了一座古老神庙的遗迹。)
- **priceless** `adj.` 无价的；极其珍贵的
  - 💡 **搭配/用法**: priceless treasure (无价之宝), priceless art (绝世艺术品)
  - 🔍 **例句**: The museum houses a priceless collection of gems. (该博物馆收藏了一批无价的宝石。)
- **manuscript** `n.` 手稿；原稿
  - 💡 **搭配/用法**: illuminated manuscript (泥金手抄本), original manuscript (原始手稿)
  - 🔍 **例句**: The author donated his original manuscripts to the library. (作者将他的原始手稿捐赠给了图书馆。)

### 🧩 句法骨架 (Syntax Structure)
- **【核心骨架 (Core Skeleton)】**:
  - `The ancient library contains priceless manuscripts.`
  - *(脱水提炼底层最小主干: 主语 The ancient library + 谓语 contains + 宾语 priceless manuscripts)*
- **【多维树状透视图谱 (Syntax Hierarchy Tree)】**:
```text
[主语] The ancient library (古老图书馆)
└─ [修饰语] which was built in the third century ← [定语从句 (Level 1)] 回答：建于何时？
[谓语] contains (藏有/包含)
[宾语] priceless manuscripts (珍贵手稿)
```
- **【顺读意群流 (Sense Groups / Reading Flow)】**:
  - `[The ancient library (古老图书馆)]` → `[which was built in the third century (建于三世纪的)]` → `[contains priceless manuscripts (藏有珍贵手稿)]`
"""


def clean_notes_markdown(raw: str) -> str:
    """Clean model generation artifacts, think tags, outer codeblocks, and xml wrappers."""
    if not raw:
        return ""
    # Strip <think>...</think>
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()

    # Strip <notes> and </notes> tags
    text = re.sub(r"<\/?notes>", "", text, flags=re.IGNORECASE).strip()

    # If the whole content was wrapped in ```markdown ... ```
    m_code = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$", text, flags=re.IGNORECASE)
    if m_code:
        text = m_code.group(1).strip()

    # If starts with conversational chatter before the first header
    idx = text.find("### 🎯")
    if idx > 0:
        text = text[idx:].strip()

    return text


class SmartNotesGenerator:
    """Two-tier caching (memory + SQLite) generator for Smart Whiteboard Notes."""

    def __init__(self):
        self._memory_cache: Dict[str, str] = {}
        self._nvidia_client: Optional[AsyncOpenAI] = None
        self._local_client: Optional[RoutedClient] = None
        self._init_db()

    def _init_db(self):
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sentence_smart_notes (
                        sentence_text TEXT PRIMARY KEY,
                        notes_markdown TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        engine TEXT DEFAULT ''
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_notes_created ON sentence_smart_notes(created_at DESC)")
        finally:
            conn.close()

    def _get_nvidia_client(self) -> Optional[AsyncOpenAI]:
        if not NVIDIA_API_KEY:
            return None
        if self._nvidia_client is None:
            self._nvidia_client = AsyncOpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=NVIDIA_API_KEY,
                timeout=35.0
            )
        return self._nvidia_client

    async def close(self):
        """Cleanly close NVIDIA API client."""
        if self._nvidia_client:
            try:
                await self._nvidia_client.close()
            except Exception:
                pass
            self._nvidia_client = None

    def _get_local_client(self) -> RoutedClient:
        if self._local_client is None:
            self._local_client = RoutedClient()
        return self._local_client

    def get_cached(self, sentence_text: str) -> Optional[str]:
        cleaned = sentence_text.replace("\u00a0", " ").strip()
        if not cleaned:
            return None
        # 1. Memory cache
        if cleaned in self._memory_cache:
            return self._memory_cache[cleaned]
        # 2. SQLite cache
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT notes_markdown FROM sentence_smart_notes WHERE sentence_text = ?",
                (cleaned,)
            ).fetchone()
            if row and row["notes_markdown"]:
                notes = row["notes_markdown"]
                self._memory_cache[cleaned] = notes
                return notes
        except Exception as exc:
            logger.warning(f"Error querying smart notes cache: {exc}")
        finally:
            conn.close()
        return None

    def save_cache(self, sentence_text: str, notes_markdown: str, engine: str = ""):
        cleaned = sentence_text.replace("\u00a0", " ").strip()
        if not cleaned or not notes_markdown.strip():
            return
        self._memory_cache[cleaned] = notes_markdown.strip()
        if len(self._memory_cache) > 500:
            # Evict oldest entry
            first_key = next(iter(self._memory_cache))
            self._memory_cache.pop(first_key, None)

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sentence_smart_notes (
                        sentence_text, notes_markdown, created_at, engine
                    ) VALUES (?, ?, ?, ?)
                """, (cleaned, notes_markdown.strip(), now_str, engine))
        except Exception as exc:
            logger.warning(f"Error saving smart notes to cache: {exc}")
        finally:
            conn.close()

    async def generate_notes(
        self,
        sentence_text: str,
        translation: Optional[str] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Generate or fetch Smart Whiteboard Notes for a sentence.
        Uses NVIDIA NIM with DeepL reference translation synergy and local LLM fallback.
        """
        cleaned = sentence_text.replace("\u00a0", " ").strip()
        if not cleaned:
            return {
                "success": False,
                "sentence": "",
                "notes_markdown": "",
                "cached": False,
                "engine": "",
                "error": "Empty sentence text"
            }

        # 1. Check cache if not forcing refresh
        if not force_refresh:
            cached_notes = self.get_cached(cleaned)
            if cached_notes:
                return {
                    "success": True,
                    "sentence": cleaned,
                    "notes_markdown": cached_notes,
                    "cached": True,
                    "engine": "cache"
                }

        # 2. Ensure high-quality reference Chinese translation
        chinese_trans = translation.strip() if (translation and isinstance(translation, str)) else ""
        if not is_valid_chinese_translation(cleaned, chinese_trans):
            try:
                fetched_trans = await translation_service.translate_sentence(cleaned)
                if is_valid_chinese_translation(cleaned, fetched_trans):
                    chinese_trans = fetched_trans
            except Exception as e:
                logger.warning(f"Failed to fetch translation for smart notes: {e}")

        user_content = f"Target Sentence: {cleaned}"
        if chinese_trans:
            user_content += f"\nReference Translation: {chinese_trans}"

        # 3. Try Cloud API (NVIDIA NIM)
        nv_client = self._get_nvidia_client()
        if nv_client:
            try:
                logger.info(f"Generating smart notes with NVIDIA NIM ({NVIDIA_MODEL}) for: {cleaned[:40]}...")
                resp = await nv_client.chat.completions.create(
                    model=NVIDIA_MODEL,
                    messages=[
                        {"role": "system", "content": SMART_NOTES_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.1,
                    max_tokens=1600,
                )
                raw_out = (resp.choices[0].message.content or "").strip()
                cleaned_out = clean_notes_markdown(raw_out)
                has_all_sections = all(h in cleaned_out for h in ("### 🎯", "### 📚", "### 🧩"))
                if cleaned_out and has_all_sections:
                    self.save_cache(cleaned, cleaned_out, engine="nvidia")
                    return {
                        "success": True,
                        "sentence": cleaned,
                        "notes_markdown": cleaned_out,
                        "cached": False,
                        "engine": "nvidia"
                    }
                else:
                    logger.warning(f"NVIDIA NIM output incomplete or missing sections: {cleaned_out[:100]}")
            except Exception as exc:
                logger.warning(f"NVIDIA NIM smart notes generation failed: {exc}")

        # 4. Fallback: Local LLM via RoutedClient
        logger.info(f"Falling back to local LLM for smart notes: {cleaned[:40]}...")
        local_client = self._get_local_client()
        try:
            resp = await local_client.chat.completions.create(
                model=QWEN_MODEL_NAME,
                messages=[
                    {"role": "system", "content": SMART_NOTES_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.2,
                max_tokens=1600,
            )
            raw_out = (resp.choices[0].message.content or "").strip()
            cleaned_out = clean_notes_markdown(raw_out)
            has_all_sections = all(h in cleaned_out for h in ("### 🎯", "### 📚", "### 🧩"))
            if cleaned_out and has_all_sections:
                self.save_cache(cleaned, cleaned_out, engine="local")
                return {
                    "success": True,
                    "sentence": cleaned,
                    "notes_markdown": cleaned_out,
                    "cached": False,
                    "engine": "local"
                }
            elif cleaned_out:
                logger.warning(f"Local LLM output incomplete or missing sections: {cleaned_out[:100]}")
        except Exception as exc:
            logger.error(f"Local LLM smart notes generation failed: {exc}")

        return {
            "success": False,
            "sentence": cleaned,
            "notes_markdown": "",
            "cached": False,
            "engine": "failed",
            "error": "All notes generation engines failed"
        }


# Global Singleton
smart_notes_generator = SmartNotesGenerator()
