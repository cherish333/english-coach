"""Tests for Cloud API Smart Whiteboard Notes generation and caching."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from src.server import app
from src.core.notes_generator import (
    smart_notes_generator,
    clean_notes_markdown,
    SmartNotesGenerator,
)
from src.core.notes_manager import get_db_connection

client = TestClient(app)


def test_clean_notes_markdown():
    """Verify clean_notes_markdown removes think tags, notes tags, outer codeblocks, and conversational intro."""
    raw = """<think>
Let's analyze this sentence.
</think>
<notes>
Here is the breakdown:
### 🎯 核心原句与释义 (Sentence)
- **原句**: `This is a test.`
- **中文释义**: 这是一个测试。
</notes>"""
    cleaned = clean_notes_markdown(raw)
    assert "<think>" not in cleaned
    assert "</notes>" not in cleaned
    assert cleaned.startswith("### 🎯 核心原句与释义 (Sentence)")
    assert "- **中文释义**: 这是一个测试。" in cleaned


def test_clean_notes_markdown_outer_codeblocks():
    """Verify stripping outer ```markdown ... ``` wrapper."""
    raw = """```markdown
### 🎯 核心原句与释义 (Sentence)
- **原句**: `Knowledge is power.`
- **中文释义**: 知识就是力量。
```"""
    cleaned = clean_notes_markdown(raw)
    assert cleaned.startswith("### 🎯 核心原句与释义 (Sentence)")
    assert not cleaned.endswith("```")


def test_smart_notes_sqlite_and_memory_caching():
    """Verify save_cache and get_cached correctly interact with memory and SQLite."""
    generator = SmartNotesGenerator()
    test_sentence = "The future belongs to those who prepare for it today."
    test_markdown = "### 🎯 核心原句与释义 (Sentence)\n- **原句**: `The future belongs to those who prepare for it today.`\n- **中文释义**: 未来属于今天做好准备的人。"

    # Save to cache
    generator.save_cache(test_sentence, test_markdown, engine="test")

    # In-memory retrieval
    assert generator.get_cached(test_sentence) == test_markdown

    # Direct SQLite query verification
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT notes_markdown, engine FROM sentence_smart_notes WHERE sentence_text = ?",
            (test_sentence,)
        ).fetchone()
        assert row is not None
        assert row["notes_markdown"] == test_markdown
        assert row["engine"] == "test"
    finally:
        conn.close()

    # Clear memory cache to ensure SQLite fallback retrieval works
    generator._memory_cache.clear()
    assert generator.get_cached(test_sentence) == test_markdown


def test_generate_notes_uses_cache():
    """Verify generate_notes returns cached notes without calling external API."""
    import asyncio

    async def _test():
        generator = SmartNotesGenerator()
        test_sentence = "Simplicity is the ultimate sophistication."
        test_markdown = "### 🎯 核心原句与释义 (Sentence)\n- **原句**: `Simplicity is the ultimate sophistication.`\n- **中文释义**: 简单是终极的精纯。"

        generator.save_cache(test_sentence, test_markdown, engine="preset")

        result = await generator.generate_notes(test_sentence, force_refresh=False)
        assert result["success"] is True
        assert result["cached"] is True
        assert result["engine"] == "cache"
        assert result["notes_markdown"] == test_markdown

    asyncio.run(_test())


def test_smart_notes_api_endpoint_empty():
    """Verify endpoint handles empty or whitespace input."""
    resp = client.post("/api/sentence/smart-notes", json={"sentence": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "Empty sentence" in data["error"]


def test_smart_notes_api_endpoint_cached():
    """Verify endpoint returns cached notes correctly."""
    sent = "A journey of a thousand miles begins with a single step."
    md = "### 🎯 核心原句与释义 (Sentence)\n- **原句**: `A journey of a thousand miles begins with a single step.`\n- **中文释义**: 千里之行，始于足下。"
    smart_notes_generator.save_cache(sent, md, engine="test")

    resp = client.post("/api/sentence/smart-notes", json={"sentence": sent, "force_refresh": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["cached"] is True
    assert data["notes_markdown"] == md


def test_translation_status_endpoint_reports_smart_notes():
    """Verify /api/translation/status includes active_smart_notes_engine."""
    resp = client.get("/api/translation/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_smart_notes_engine" in data
    assert data["active_smart_notes_engine"] in ("NVIDIA NIM", "Local Model")
