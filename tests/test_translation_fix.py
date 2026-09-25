"""Regression tests for sentence translation bug fix, Chinese validation, and cache safety."""

import pytest
import sqlite3
import re
from fastapi.testclient import TestClient
from src.server import app
from src.core.translation import (
    TranslationService,
    is_valid_chinese_translation,
    clean_translation_output,
)
from src.core.notes_manager import DB_PATH, get_db_connection

client = TestClient(app)


def test_is_valid_chinese_translation():
    """Verify validation rejects empty, identical English echoes, or non-Chinese outputs for English inputs."""
    target_en = "You have to turn these off to be able to install Omarchy."

    # 1. Identical English echo must be rejected
    assert is_valid_chinese_translation(target_en, target_en) is False
    assert is_valid_chinese_translation(target_en, target_en.lower()) is False

    # 2. English text with different phrasing but NO Chinese characters must be rejected
    assert is_valid_chinese_translation(target_en, "Turn off these options to install Omarchy.") is False

    # 3. Empty or whitespace translation must be rejected
    assert is_valid_chinese_translation(target_en, "") is False
    assert is_valid_chinese_translation(target_en, "   ") is False
    assert is_valid_chinese_translation(target_en, None) is False

    # 4. Genuine Chinese translation must be accepted
    assert is_valid_chinese_translation(target_en, "你需要关闭这些才能安装 Omarchy。") is True
    assert is_valid_chinese_translation(target_en, "您必须关闭这些功能才能安装 Omarchy。") is True

    # 5. Short English sentence with Chinese translation
    assert is_valid_chinese_translation("Hello world", "你好，世界") is True
    assert is_valid_chinese_translation("Hello world", "Hello world") is False


def test_clean_translation_output():
    """Verify cleaning removes think tokens, markdown codeblocks, label prefixes, and quotes."""
    raw_with_think = "<think>Translate carefully</think>你需要关闭这些设置。"
    assert clean_translation_output(raw_with_think) == "你需要关闭这些设置。"

    raw_with_codeblock = "```json\n[\"你好，世界！\"]\n```"
    assert clean_translation_output(raw_with_codeblock) == "[\"你好，世界！\"]"

    raw_with_prefix = "翻译：这是中文译文。"
    assert clean_translation_output(raw_with_prefix) == "这是中文译文。"

    raw_with_trans_prefix = "Translation: 这是中文译文。"
    assert clean_translation_output(raw_with_trans_prefix) == "这是中文译文。"

    # Bold markdown prefix variants
    assert clean_translation_output("**翻译**：你需要关闭这些。") == "你需要关闭这些。"
    assert clean_translation_output("**Translation:** \"你需要关闭这些。\"") == "你需要关闭这些。"
    assert clean_translation_output("**中文翻译**: “你需要关闭这些。”") == "你需要关闭这些。"

    raw_with_quotes = "“这是带引号的译文”"
    assert clean_translation_output(raw_with_quotes) == "这是带引号的译文"

    # Repeated / nested quotes
    assert clean_translation_output("“‘这是嵌套引号’‘”") == "这是嵌套引号"


def test_poisoned_cache_eviction_and_sanitization():
    """Verify that poisoned rows (English echo cached as Chinese) are evicted and not returned."""
    ts = TranslationService()
    test_src = "A uniquely poisoned sentence for testing eviction."
    poisoned_trans = test_src  # Raw English echo

    # Manually insert poisoned entry into DB
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sentence_translations (source_text, target_lang, translation, created_at)
                VALUES (?, 'zh', ?, datetime('now'))
                """,
                (test_src, poisoned_trans),
            )
    finally:
        conn.close()

    # get_cached must detect invalid translation, purge it from DB, and return None
    cached = ts.get_cached(test_src)
    assert cached is None, f"Expected None for poisoned cache, got: {cached}"

    # Verify it was removed from SQLite
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT translation FROM sentence_translations WHERE source_text = ? AND target_lang = 'zh'",
            (test_src,),
        ).fetchone()
        assert row is None, "Poisoned entry should have been deleted from SQLite"
    finally:
        conn.close()

    # Verify save_cache rejects saving invalid translation
    ts.save_cache(test_src, poisoned_trans)
    assert ts.get_cached(test_src) is None


@pytest.mark.anyio
async def test_translate_sentence_target_omarchy():
    """Verify that translating the target sentence returns genuine Chinese characters."""
    ts = TranslationService()
    sentence = "You have to turn these off to be able to install Omarchy."
    result = await ts.translate_sentence(sentence)
    assert isinstance(result, str) and result.strip()
    assert result.strip().lower() != sentence.lower()
    assert bool(re.search(r"[\u4e00-\u9fa5]", result)), f"Expected Chinese characters, got: {result}"


def test_translate_api_endpoint():
    """Verify /api/translate returns genuine Chinese and handles both single and batch."""
    target_sentence = "You have to turn these off to be able to install Omarchy."

    # 1. Single sentence test
    resp = client.post("/api/translate", json={"text": target_sentence})
    assert resp.status_code == 200
    data = resp.json()
    assert data["original"] == target_sentence
    trans = data["translation"]
    assert trans.strip().lower() != target_sentence.lower()
    assert bool(re.search(r"[\u4e00-\u9fa5]", trans)), f"Expected Chinese characters, got: {trans}"

    # 2. Batch sentences test
    batch_sentences = [
        "Omarchy is installed using an ISO.",
        "You have to turn these off to be able to install Omarchy.",
    ]
    resp_batch = client.post("/api/translate", json={"texts": batch_sentences})
    assert resp_batch.status_code == 200
    b_data = resp_batch.json()
    assert "translations" in b_data
    for s in batch_sentences:
        t = b_data["translations"].get(s)
        assert t, f"Missing translation for: {s}"
        assert t.strip().lower() != s.lower()
        assert bool(re.search(r"[\u4e00-\u9fa5]", t)), f"Expected Chinese characters for {s}, got: {t}"


def test_translation_status_endpoint():
    """Verify /api/translation/status returns engine configuration and cache metrics."""
    resp = client.get("/api/translation/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "engine_preference" in data
    assert "active_sentence_engine" in data
    assert "active_glosses_engine" in data
    assert "deepl_configured" in data
    assert data["deepl_configured"] is True
    assert "nvidia_configured" in data
    assert data["nvidia_configured"] is True


@pytest.mark.anyio
async def test_deepl_fallback_behavior(monkeypatch):
    """Verify that when DeepL returns None or fails, translation service falls back cleanly."""
    ts = TranslationService()

    # Mock DeepL to fail
    async def mock_failed_deepl(texts):
        return None

    monkeypatch.setattr(ts, "_translate_with_deepl", mock_failed_deepl)

    # Mock local client to return valid translation
    from unittest.mock import AsyncMock, MagicMock
    mock_choice = MagicMock()
    mock_choice.message.content = "本地降级测试：你好，世界！"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
    monkeypatch.setattr(ts, "_get_client", lambda: mock_client)

    result = await ts.translate_sentence("DeepL failure fallback test sentence.")
    assert "你好" in result or "测试" in result

