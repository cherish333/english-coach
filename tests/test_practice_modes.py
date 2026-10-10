"""Tests for the practice modes (copy / cloze / recall / dictation) and the
spaced-repetition API surface.

The learning-science point these defend: copy-typing with the answer visible
requires no retrieval, so it must not be treated as evidence of learning — and
the modes that *do* require retrieval must actually hide the answer.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import PROJECT_ROOT
from src.server import app

client = TestClient(app)
APP_JS = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (PROJECT_ROOT / "src" / "static" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Server-side cloze generation
# ---------------------------------------------------------------------------

def test_cloze_endpoint_hides_content_words():
    resp = client.post(
        "/api/practice/cloze",
        json={"text": "The ancient library contains priceless manuscripts.", "hide_ratio": 0.5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["hidden_words"], "cloze hid nothing"
    masked = data["masked_text"]
    for word in data["hidden_words"]:
        assert "_" * 3 in masked
        assert f" {word} " not in f" {masked} "
    # Function words stay visible so the sentence skeleton remains readable.
    assert "this" in masked or "the" in masked.lower()


def test_cloze_endpoint_rejects_empty_text():
    assert client.post("/api/practice/cloze", json={"text": ""}).status_code == 400


def test_dictation_endpoint_grades_and_reports_errors():
    resp = client.post(
        "/api/practice/grade-dictation",
        json={
            "target_text": "I have been learning English for three years.",
            "typed_text": "I have learning English for three years.",
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["score"] < 100
    assert any(e["type"] == "omission" and e["expected"] == "been" for e in result["errors"])
    assert result["grade"] in (1, 2, 3, 4)


def test_dictation_endpoint_requires_target():
    # An empty body field fails Pydantic validation (422); whitespace-only content
    # passes validation but is rejected by the handler (400).
    assert client.post("/api/practice/grade-dictation", json={}).status_code == 422
    assert client.post(
        "/api/practice/grade-dictation", json={"target_text": "", "typed_text": "x"}
    ).status_code == 400
    assert client.post(
        "/api/practice/grade-dictation", json={"target_text": "   ", "typed_text": "x"}
    ).status_code == 400


def test_dictation_input_is_length_capped():
    resp = client.post(
        "/api/practice/grade-dictation",
        json={"target_text": "x" * 5000, "typed_text": ""},
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# SRS API surface
# ---------------------------------------------------------------------------

def test_review_queue_and_stats_shape():
    queue = client.get("/api/review/queue?limit=5")
    assert queue.status_code == 200
    body = queue.json()
    assert set(body) >= {"items", "stats", "count"}
    assert isinstance(body["items"], list)

    stats = client.get("/api/review/stats?days=7").json()
    assert set(stats) >= {"stats", "forecast", "mastery"}
    assert len(stats["forecast"]) == 8  # today + 7
    assert "known" in stats["mastery"]


def test_review_grade_rejects_invalid_rating():
    resp = client.post("/api/review/grade", json={"item_id": 1, "rating": 9})
    assert resp.status_code == 400


def test_review_grade_404s_for_unknown_item():
    resp = client.post("/api/review/grade", json={"item_id": 99999999, "rating": 3})
    assert resp.status_code == 404


def test_review_seed_endpoint_is_idempotent():
    first = client.post("/api/review/seed")
    second = client.post("/api/review/seed")
    assert first.status_code == 200 and second.status_code == 200
    # Re-seeding must not duplicate or reset items.
    assert first.json()["stats"]["total"] == second.json()["stats"]["total"]


def test_review_queue_limit_is_clamped():
    assert client.get("/api/review/queue?limit=9999").status_code == 200
    assert client.get("/api/review/queue?limit=-5").status_code == 200


# ---------------------------------------------------------------------------
# Frontend practice-mode wiring
# ---------------------------------------------------------------------------

def test_practice_modes_are_defined():
    for mode in ("copy", "cloze", "recall", "dictation"):
        assert f'"{mode}"' in APP_JS or f"'{mode}'" in APP_JS
    assert "PRACTICE_MODES" in APP_JS
    assert "function cyclePracticeMode(" in APP_JS
    assert "function setPracticeMode(" in APP_JS


def test_retrieval_modes_actually_hide_the_answer():
    """recall/dictation must mask characters; copy must not."""
    assert "char-hidden" in APP_JS
    assert "char-current-masked" in APP_JS
    # dictation hides everything, recall keeps first letters.
    assert 'currentPracticeMode === "dictation"' in APP_JS
    assert 'currentPracticeMode === "recall"' in APP_JS or "isRecall" in APP_JS


def test_masked_rendering_uses_the_typed_buffer_not_the_dom():
    """Regression guard: reading typed text from the DOM would include the mask."""
    assert "const typed = typedBuffer;" in APP_JS
    assert "const typed = (display.textContent" not in APP_JS


def test_copy_mode_must_not_feed_the_scheduler():
    """Copy-typing is transcription, so it must not be recorded as retrieval."""
    assert 'if (currentPracticeMode !== "copy")' in APP_JS
    assert "recordRetrievalResult" in APP_JS


def test_revealing_the_answer_downgrades_the_grade():
    assert "typingRevealCount" in APP_JS
    assert "grade = Math.min(grade, 2)" in APP_JS


def test_review_tab_exists_in_the_ui():
    assert 'id="tab-content-review"' in INDEX_HTML
    assert 'id="modal-review-list"' in INDEX_HTML
    assert 'data-tab="review"' in INDEX_HTML
    assert "loadReviewPanel" in APP_JS
    assert "loadReviewForecast" in APP_JS


def test_practice_mode_shortcut_and_button_exist():
    assert 'id="typing-mode-btn"' in INDEX_HTML
    assert 'id="typing-reveal-btn"' in INDEX_HTML
    assert 'e.code === "KeyM"' in APP_JS


def test_translation_is_hidden_in_retrieval_modes():
    """The Chinese translation gives the answer away during recall."""
    assert 'transDisplay.style.display = mode === "copy" ? "" : "none"' in APP_JS
    assert "currentPracticeMode !== \"copy\"" in APP_JS  # PDF highlight suppression


def test_dictation_autoplays_the_sentence():
    """A listening exercise with no audio is not a listening exercise."""
    assert 'currentPracticeMode === "dictation"' in APP_JS
    assert "replayCurrentSentenceAudio()" in APP_JS
