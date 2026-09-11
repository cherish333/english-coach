import pytest
from starlette.testclient import TestClient
from src.server import app
from src.core.progress import ProgressManager, init_progress_db
from src.core.notes_manager import get_db_connection


def setup_function():
    """Ensure tables exist and clear test state before each test."""
    init_progress_db()
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM user_progress")
        conn.execute("DELETE FROM document_progress")


def test_progress_manager_defaults():
    prog = ProgressManager.get_progress()
    assert prog["last_document_id"] == "vocabulary"
    assert prog["last_page"] == 1
    assert prog["last_sentence_index"] == 1
    assert prog["teaching_style"] == "spoken"
    assert prog["books"] == {}


def test_progress_manager_save_and_restore():
    # Save progress for western civilization
    saved = ProgressManager.save_progress(
        document_id="west_civ",
        page=42,
        sentence_index=6,
        teaching_style="history"
    )
    assert saved["last_document_id"] == "west_civ"
    assert saved["last_page"] == 42
    assert saved["last_sentence_index"] == 6
    assert saved["teaching_style"] == "history"
    assert "west_civ" in saved["books"]
    assert saved["books"]["west_civ"]["last_page"] == 42
    assert saved["books"]["west_civ"]["last_sentence_index"] == 6

    # Verify retrieval
    retrieved = ProgressManager.get_progress()
    assert retrieved["last_document_id"] == "west_civ"
    assert retrieved["last_page"] == 42
    assert retrieved["last_sentence_index"] == 6
    assert retrieved["teaching_style"] == "history"
    assert retrieved["books"]["west_civ"]["last_page"] == 42


def test_progress_manager_multiple_books():
    # Progress in vocabulary
    ProgressManager.save_progress(
        document_id="vocabulary",
        page=15,
        sentence_index=3,
        teaching_style="spoken"
    )
    # Then study western civilization
    ProgressManager.save_progress(
        document_id="west_civ",
        page=38,
        sentence_index=5,
        teaching_style="history"
    )

    prog = ProgressManager.get_progress()
    assert prog["last_document_id"] == "west_civ"
    assert prog["last_page"] == 38
    assert prog["last_sentence_index"] == 5
    assert prog["teaching_style"] == "history"

    # Both books should have their independent progress preserved
    assert prog["books"]["vocabulary"]["last_page"] == 15
    assert prog["books"]["vocabulary"]["last_sentence_index"] == 3
    assert prog["books"]["west_civ"]["last_page"] == 38
    assert prog["books"]["west_civ"]["last_sentence_index"] == 5


def test_progress_api_endpoints():
    client = TestClient(app)

    # 1. GET initial progress
    res = client.get("/api/progress")
    assert res.status_code == 200
    data = res.json()
    assert "last_document_id" in data
    assert "last_page" in data
    assert "books" in data

    # 2. POST update progress
    payload = {
        "document_id": "west_civ",
        "page": 55,
        "sentence_index": 8,
        "teaching_style": "history"
    }
    res_post = client.post("/api/progress", json=payload)
    assert res_post.status_code == 200
    post_data = res_post.json()
    assert post_data["success"] is True
    assert post_data["progress"]["last_document_id"] == "west_civ"
    assert post_data["progress"]["last_page"] == 55
    assert post_data["progress"]["last_sentence_index"] == 8
    assert post_data["progress"]["teaching_style"] == "history"

    # 3. Verify GET returns updated data
    res_get = client.get("/api/progress")
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["last_document_id"] == "west_civ"
    assert get_data["last_page"] == 55
    assert get_data["last_sentence_index"] == 8
    assert get_data["teaching_style"] == "history"
    assert get_data["books"]["west_civ"]["last_page"] == 55
