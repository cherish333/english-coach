"""Tests for web import functionality and architectural stability guarantees."""

import pytest
import sqlite3
import re
from pathlib import Path
from fastapi.testclient import TestClient
from src.server import app
from src.core.notes_manager import get_db_connection, DB_PATH
from src.core.web_importer import normalize_typography, build_article_pdf, clean_html_article

client = TestClient(app)


def test_sqlite_wal_and_busy_timeout():
    """Verify that SQLite connection operates in WAL mode with sufficient busy timeout."""
    conn = get_db_connection()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"

        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert busy_timeout >= 5000, f"Expected busy_timeout >= 5000ms, got {busy_timeout}"
    finally:
        conn.close()


def test_typography_normalization():
    """Verify smart quotes and typography dashes are normalized to standard ASCII for typing."""
    raw = "“It’s a test—really,” said ‘John’…\u00a0done."
    normalized = normalize_typography(raw)
    assert "“" not in normalized
    assert "”" not in normalized
    assert "’" not in normalized
    assert "‘" not in normalized
    assert "—" not in normalized
    assert normalized == '"It\'s a test - really," said \'John\'... done.'


def test_clean_html_article_extraction():
    """Verify HTML cleaner strips script/nav/footer and extracts title and content."""
    dummy_html = """
    <html>
      <head><title>Clean Architecture Guide</title></head>
      <body>
        <nav><a href="/home">Home</a></nav>
        <article>
          <h1>Clean Architecture Guide</h1>
          <p>Architecture represents the significant design decisions that shape a system.</p>
          <p>A solid foundation prevents cascading failures and ensures maintainability.</p>
        </article>
        <footer><p>Copyright 2026</p></footer>
      </body>
    </html>
    """
    title, paras = clean_html_article(dummy_html, fallback_url="https://example.com/guide")
    assert "Clean Architecture Guide" in title
    # First paragraph repeating title should be deduped
    assert len(paras) == 2
    assert "Architecture represents" in paras[0]
    assert "Copyright" not in " ".join(paras)


def test_build_article_pdf_structure():
    """Verify PDF generator creates valid PDF bytes with proper page count."""
    paras = [
        "Paragraph one is informative and concise.",
        "Paragraph two provides additional context for the reader.",
    ]
    pdf_bytes, page_count = build_article_pdf("Test Title", paras, url="https://example.com")
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")
    assert page_count >= 1


def test_import_url_direct_text_endpoint():
    """Test importing text directly through /api/documents/import-url and subsequent deletion."""
    payload = {
        "title": "System Stability Principles",
        "raw_text": "Principle 1: Fail fast and isolate failures.\n\nPrinciple 2: Design with timeouts and bounded queues.",
    }
    resp = client.post("/api/documents/import-url", json=payload)
    assert resp.status_code == 200
    doc = resp.json()["document"]
    doc_id = doc["id"]
    assert "System_Stability" in doc_id

    # Verify page retrieval
    p_resp = client.get(f"/api/documents/{doc_id}/pages/1/sentences")
    assert p_resp.status_code == 200
    sentences = p_resp.json()["sentences"]
    assert len(sentences) >= 2

    # Clean up
    del_resp = client.delete(f"/api/documents/{doc_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True


def test_no_duplicate_javascript_function_names():
    """Verify app.js has zero duplicate function declarations."""
    app_js_path = Path("src/static/app.js")
    assert app_js_path.is_file()
    content = app_js_path.read_text(encoding="utf-8")
    funcs = re.findall(r"function\s+([a-zA-Z0-9_$]+)\s*\(", content)
    seen = set()
    duplicates = []
    for f in funcs:
        if f in seen:
            duplicates.append(f)
        seen.add(f)
    assert duplicates == [], f"Found duplicate function declarations in app.js: {duplicates}"
