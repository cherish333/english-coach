import sqlite3
import datetime
import logging
from typing import Dict, Any, Optional
from src.core.notes_manager import get_db_connection

logger = logging.getLogger("english_coach.progress")


def init_progress_db():
    """Ensure user progress tables exist in SQLite database."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_progress (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    last_document_id TEXT DEFAULT 'vocabulary',
                    last_page INTEGER DEFAULT 1,
                    last_sentence_index INTEGER DEFAULT 1,
                    teaching_style TEXT DEFAULT 'spoken',
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_progress (
                    document_id TEXT PRIMARY KEY,
                    last_page INTEGER DEFAULT 1,
                    last_sentence_index INTEGER DEFAULT 1,
                    updated_at TEXT
                )
            """)
    finally:
        conn.close()

# Auto-initialize on import
init_progress_db()


class ProgressManager:
    """Manages learning progress across sessions, books, pages, and sentences."""

    @staticmethod
    def get_progress() -> Dict[str, Any]:
        conn = get_db_connection()
        try:
            row = conn.execute("SELECT * FROM user_progress WHERE id = 1").fetchone()
            doc_rows = conn.execute("SELECT * FROM document_progress").fetchall()

            books = {}
            for dr in doc_rows:
                books[dr["document_id"]] = {
                    "last_page": dr["last_page"],
                    "last_sentence_index": dr["last_sentence_index"],
                    "updated_at": dr["updated_at"],
                }

            if not row:
                return {
                    "last_document_id": "vocabulary",
                    "last_page": 1,
                    "last_sentence_index": 1,
                    "teaching_style": "spoken",
                    "updated_at": None,
                    "books": books,
                }

            return {
                "last_document_id": row["last_document_id"] or "vocabulary",
                "last_page": row["last_page"] or 1,
                "last_sentence_index": row["last_sentence_index"] or 1,
                "teaching_style": row["teaching_style"] or "spoken",
                "updated_at": row["updated_at"],
                "books": books,
            }
        finally:
            conn.close()

    @staticmethod
    def save_progress(
        document_id: Optional[str] = None,
        page: Optional[int] = None,
        sentence_index: Optional[int] = None,
        teaching_style: Optional[str] = None,
    ) -> Dict[str, Any]:
        conn = get_db_connection()
        now = datetime.datetime.now().isoformat()

        try:
            with conn:
                row = conn.execute("SELECT * FROM user_progress WHERE id = 1").fetchone()
                current_doc = row["last_document_id"] if row else "vocabulary"
                current_page = row["last_page"] if row else 1
                current_sentence = row["last_sentence_index"] if row else 1

                # When switching documents, use the target document's saved progress
                # instead of the current global position to avoid cross-book contamination.
                if document_id is not None and (document_id != current_doc or page is None or sentence_index is None):
                    doc_row = conn.execute(
                        "SELECT last_page, last_sentence_index FROM document_progress WHERE document_id = ?",
                        (document_id,)
                    ).fetchone()
                    if doc_row:
                        if page is None:
                            current_page = doc_row["last_page"]
                        if sentence_index is None:
                            current_sentence = doc_row["last_sentence_index"]
                    else:
                        if page is None:
                            current_page = 1
                        if sentence_index is None:
                            current_sentence = 1
                current_style = row["teaching_style"] if row else "spoken"

                new_doc = str(document_id).strip() if document_id else current_doc
                try:
                    new_page = max(1, int(page)) if page is not None else current_page
                except (ValueError, TypeError):
                    new_page = current_page

                try:
                    new_sentence = max(1, int(sentence_index)) if sentence_index is not None else current_sentence
                except (ValueError, TypeError):
                    new_sentence = current_sentence

                new_style = str(teaching_style).strip() if teaching_style else current_style

                conn.execute("""
                    INSERT INTO user_progress (id, last_document_id, last_page, last_sentence_index, teaching_style, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_document_id = excluded.last_document_id,
                        last_page = excluded.last_page,
                        last_sentence_index = excluded.last_sentence_index,
                        teaching_style = excluded.teaching_style,
                        updated_at = excluded.updated_at
                """, (new_doc, new_page, new_sentence, new_style, now))

                if new_doc:
                    conn.execute("""
                        INSERT INTO document_progress (document_id, last_page, last_sentence_index, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(document_id) DO UPDATE SET
                            last_page = excluded.last_page,
                            last_sentence_index = excluded.last_sentence_index,
                            updated_at = excluded.updated_at
                    """, (new_doc, new_page, new_sentence, now))
        finally:
            conn.close()

        logger.info(f"Progress saved: doc={new_doc}, page={new_page}, sent={new_sentence}, style={new_style}")
        return ProgressManager.get_progress()

