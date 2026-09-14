import pytest
from fastapi.testclient import TestClient
from src.server import app
from src.core.gamification import GamificationManager, init_gamification_db
from src.core.notes_manager import get_db_connection

client = TestClient(app)


def test_odometer_db_schema_and_migration():
    """Verify typed_words_log table exists and user_gamification has words_typed."""
    init_gamification_db()
    conn = get_db_connection()
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "user_gamification" in tables
        assert "typed_words_log" in tables

        user_cols = [r[1] for r in conn.execute("PRAGMA table_info(user_gamification)").fetchall()]
        assert "words_typed" in user_cols

        log_cols = [r[1] for r in conn.execute("PRAGMA table_info(typed_words_log)").fetchall()]
        assert "word" in log_cols
        assert "clean_word" in log_cols
        assert "date_str" in log_cols
        assert "created_at" in log_cols
        assert "document_id" in log_cols
        assert "sentence_index" in log_cols
    finally:
        conn.close()


def test_odometer_record_words_typed():
    """Test recording words through GamificationManager."""
    initial_stats = GamificationManager.get_odometer_stats()
    initial_total = initial_stats["total_words"]

    # Record 3 words
    res = GamificationManager.record_words_typed(
        words=["Hello", "beautiful", "world!"],
        document_id="unit_1",
        sentence_index=2
    )
    assert res["success"] is True
    assert res["words_recorded"] == 3
    assert res["total_words"] == initial_total + 3
    assert res["today_words"] >= 3

    # Check persistence in DB
    updated_stats = GamificationManager.get_odometer_stats()
    assert updated_stats["total_words"] == initial_total + 3
    recent_words = [r["word"].lower() for r in updated_stats["recent_words"]]
    assert "hello" in recent_words
    assert "world" in recent_words


def test_odometer_record_edge_cases():
    """Test empty, whitespace, and special characters."""
    initial_stats = GamificationManager.get_odometer_stats()
    initial_total = initial_stats["total_words"]

    # Empty list
    res_empty = GamificationManager.record_words_typed([])
    assert res_empty["success"] is True
    assert res_empty["words_recorded"] == 0
    assert res_empty["total_words"] == initial_total

    # Punctuation only
    res_punct = GamificationManager.record_words_typed(["...", "!!!", "  "])
    assert res_punct["success"] is True
    assert res_punct["words_recorded"] == 0
    assert res_punct["total_words"] == initial_total

    # Hyphenated and apostrophe words
    res_special = GamificationManager.record_words_typed(["state-of-the-art", "don't"])
    assert res_special["success"] is True
    assert res_special["words_recorded"] == 2
    assert res_special["total_words"] == initial_total + 2


def test_odometer_api_get_stats():
    """Test GET /api/game/odometer endpoint."""
    res = client.get("/api/game/odometer")
    assert res.status_code == 200
    data = res.json()
    assert "total_words" in data
    assert "today_words" in data
    assert "unique_words" in data
    assert "recent_words" in data
    assert "top_words" in data
    assert isinstance(data["recent_words"], list)
    assert isinstance(data["top_words"], list)


def test_odometer_api_record_batch():
    """Test POST /api/game/odometer/record endpoint with word batch."""
    initial = client.get("/api/game/odometer").json()["total_words"]

    res = client.post("/api/game/odometer/record", json={
        "words": ["Python", "FastAPI", "SQLite"],
        "document_id": "test_doc",
        "sentence_index": 1
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["words_recorded"] == 3
    assert data["total_words"] == initial + 3


def test_odometer_api_record_single_word():
    """Test POST /api/game/odometer/record endpoint with single word."""
    initial = client.get("/api/game/odometer").json()["total_words"]

    res = client.post("/api/game/odometer/record", json={
        "word": "odometer"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["words_recorded"] == 1
    assert data["total_words"] == initial + 1


def test_odometer_no_double_counting():
    """Test that increment_words=False in typing_completed avoids double counting."""
    initial = client.get("/api/game/odometer").json()["total_words"]

    # 1. Simulate word-by-word typing: 2 words recorded
    res_rec = client.post("/api/game/odometer/record", json={
        "words": ["artificial", "intelligence"]
    })
    assert res_rec.status_code == 200
    assert res_rec.json()["total_words"] == initial + 2

    # 2. Sentence completed with increment_words=False
    res_act = client.post("/api/game/action", json={
        "action_type": "typing_completed",
        "sentence_text": "artificial intelligence",
        "words_count": 2,
        "combo": 2,
        "increment_words": False
    })
    assert res_act.status_code == 200
    # Words should NOT increase again
    after_stats = client.get("/api/game/odometer").json()
    assert after_stats["total_words"] == initial + 2


def test_odometer_action_word_typed():
    """Test POST /api/game/action with action_type=word_typed."""
    initial = client.get("/api/game/odometer").json()["total_words"]

    res = client.post("/api/game/action", json={
        "action_type": "word_typed",
        "words_count": 1,
        "words_list": ["spectacular"]
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["result"]["words_typed"] == initial + 1


def test_game_status_includes_odometer_fields():
    """Test GET /api/game/status returns words_typed, today_words, unique_words."""
    res = client.get("/api/game/status")
    assert res.status_code == 200
    data = res.json()
    assert "words_typed" in data
    assert "today_words" in data
    assert "unique_words" in data
    assert data["words_typed"] >= 0
    assert data["today_words"] >= 0


def test_odometer_payload_edge_cases():
    """Test None, mixed types, and empty json bodies."""
    # Direct manager call with None
    res_none = GamificationManager.record_words_typed(None)
    assert res_none["success"] is True
    assert res_none["words_recorded"] == 0

    # API with empty json body
    res_empty = client.post("/api/game/odometer/record", json={})
    assert res_empty.status_code == 200
    assert res_empty.json()["words_recorded"] == 0

    # API with mixed types in words list
    res_mixed = client.post("/api/game/odometer/record", json={"words": [123, None, "resilience", {}]})
    assert res_mixed.status_code == 200
    assert res_mixed.json()["words_recorded"] == 1
