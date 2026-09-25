import sqlite3
import datetime
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from src.config import PROJECT_ROOT


DB_PATH = PROJECT_ROOT / "data" / "coach_notes.db"

def get_db_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn

def init_db():
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    document_id TEXT,
                    document_title TEXT,
                    unit_number INTEGER,
                    page_number INTEGER,
                    sentence_index INTEGER,
                    sentence_text TEXT,
                    title TEXT,
                    voice_text TEXT,
                    notes_markdown TEXT NOT NULL,
                    category TEXT DEFAULT 'lecture',
                    is_mistake INTEGER DEFAULT 0,
                    mistake_type TEXT DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_doc_page ON notes(document_id, page_number)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_mistake ON notes(is_mistake)")

            # Migration: ensure is_mistake and mistake_type columns exist
            cursor = conn.execute("PRAGMA table_info(notes)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            if "is_mistake" not in existing_cols:
                conn.execute("ALTER TABLE notes ADD COLUMN is_mistake INTEGER DEFAULT 0")
            if "mistake_type" not in existing_cols:
                conn.execute("ALTER TABLE notes ADD COLUMN mistake_type TEXT DEFAULT ''")
    finally:
        conn.close()

# Initialize upon import
init_db()


class NotesManager:
    """Manages persistent learning notes and export functionality."""

    @staticmethod
    def save_note(
        notes_markdown: str,
        voice_text: str = "",
        document_id: Optional[str] = None,
        document_title: Optional[str] = None,
        unit_number: Optional[int] = None,
        page_number: Optional[int] = None,
        sentence_index: Optional[int] = None,
        sentence_text: Optional[str] = None,
        title: Optional[str] = None,
        category: str = "lecture",
        is_mistake: int = 0,
        mistake_type: str = ""
    ) -> Dict[str, Any]:
        if not notes_markdown or not notes_markdown.strip():
            raise ValueError("Notes markdown content cannot be empty")

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not title:
            if sentence_text:
                prefix = "⚠️ 错题强化: " if is_mistake else "句研: "
                title = f"{prefix}{sentence_text[:35]}..." if len(sentence_text) > 35 else f"{prefix}{sentence_text}"
            elif document_title and page_number:
                title = f"{document_title} · P{page_number}"
            else:
                title = f"口语随堂笔记 {now_str}"

        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.execute("""
                    INSERT INTO notes (
                        created_at, document_id, document_title, unit_number,
                        page_number, sentence_index, sentence_text, title,
                        voice_text, notes_markdown, category, is_mistake, mistake_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now_str, document_id, document_title, unit_number,
                    page_number, sentence_index, sentence_text, title,
                    voice_text, notes_markdown.strip(), category, is_mistake, mistake_type
                ))
                note_id = cursor.lastrowid
        finally:
            conn.close()

        return {
            "id": note_id,
            "created_at": now_str,
            "title": title,
            "document_id": document_id,
            "document_title": document_title,
            "unit_number": unit_number,
            "page_number": page_number,
            "sentence_index": sentence_index,
            "sentence_text": sentence_text,
            "voice_text": voice_text,
            "notes_markdown": notes_markdown.strip(),
            "category": category,
            "is_mistake": is_mistake,
            "mistake_type": mistake_type,
        }

    @staticmethod
    def save_mistake(
        sentence_text: str,
        mistake_type: str = "typing",
        mistake_detail: str = "",
        document_id: Optional[str] = None,
        document_title: Optional[str] = None,
        page_number: Optional[int] = None,
        sentence_index: Optional[int] = None,
        voice_text: str = "",
        notes_markdown: str = ""
    ) -> Dict[str, Any]:
        """Record a typing or pronunciation mistake for spaced repetition review."""
        if not notes_markdown:
            notes_markdown = f"### ⚠️ 错题复习记录 ({mistake_type.upper()})\n\n- **目标句子**: `{sentence_text}`\n"
            if mistake_detail:
                notes_markdown += f"- **错误详情**: {mistake_detail}\n"
        return NotesManager.save_note(
            notes_markdown=notes_markdown,
            voice_text=voice_text,
            document_id=document_id,
            document_title=document_title,
            page_number=page_number,
            sentence_index=sentence_index,
            sentence_text=sentence_text,
            category="mistake",
            is_mistake=1,
            mistake_type=mistake_type
        )

    @staticmethod
    def list_notes(
        limit: int = 50,
        document_id: Optional[str] = None,
        only_mistakes: bool = False,
        max_limit: int = 200
    ) -> List[Dict[str, Any]]:
        try:
            safe_limit = max(1, min(int(limit), max_limit))
        except (ValueError, TypeError):
            safe_limit = 50

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            query = "SELECT * FROM notes WHERE 1=1"
            params = []
            if document_id:
                query += " AND document_id = ?"
                params.append(document_id)
            if only_mistakes:
                query += " AND is_mistake = 1"
            query += " ORDER BY id DESC LIMIT ?"
            params.append(safe_limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def delete_note(note_id: int) -> bool:
        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                affected = cursor.rowcount
            return affected > 0
        finally:
            conn.close()

    @staticmethod
    def export_notes_markdown(document_id: Optional[str] = None) -> str:
        notes = NotesManager.list_notes(limit=500, document_id=document_id, max_limit=1000)
        if not notes:
            return "# AI English Coach · 学习笔记\n\n暂无已保存的笔记。\n"

        lines = [
            "# 📘 AI English Coach · 学习笔记汇总",
            f"> 导出时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 共 {len(notes)} 条笔记\n",
            "---",
            ""
        ]

        for n in notes:
            lines.append(f"## {n['title']}")
            meta_parts = [f"⏱️ **时间**: `{n['created_at']}`"]
            if n.get('document_title'):
                meta_parts.append(f"📚 **教材**: {n['document_title']}")
            if n.get('page_number'):
                meta_parts.append(f"📄 **页码**: 第 {n['page_number']} 页")
            if n.get('sentence_index') and n.get('sentence_text'):
                meta_parts.append(f"🎯 **原句 [{n['sentence_index']}]**: *\"{n['sentence_text']}\"*")
            if n.get('is_mistake'):
                meta_parts.append(f"⚠️ **错题分类**: `{n.get('mistake_type', 'mistake')}`")
            lines.append(" | ".join(meta_parts))
            lines.append("")

            if n.get('voice_text'):
                lines.append(f"> 🎙️ **外教讲解语音录音文字**: {n['voice_text']}\n")

            lines.append(n['notes_markdown'])
            lines.append("\n---\n")

        return "\n".join(lines)

    @staticmethod
    def export_anki_tsv(
        document_id: Optional[str] = None,
        only_mistakes: bool = False,
        notes: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Exports notes as Anki-ready Tab-Separated Values (.tsv) with HTML formatting.
        Anki import instructions:
          - Field 1: Front (Prompt / Target Sentence)
          - Field 2: Back (Whiteboard notes, grammar explanation, voice transcript)
          - Field 3: Tags
        """
        if isinstance(document_id, list):
            notes = document_id
            document_id = None
        if notes is None:
            notes = NotesManager.list_notes(limit=10000, document_id=document_id, only_mistakes=only_mistakes, max_limit=10000)
        if not notes:
            return "#separator:tab\n#html:true\n#tags column:3\n"

        def html_escape(text: str) -> str:
            if not text:
                return ""
            return (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )

        def md_to_anki_html(md: str) -> str:
            # Simple conversion of markdown headers, code blocks, and bold to clean Anki HTML
            h = html_escape(md)
            # 1. Stash fenced code blocks before inline backticks to preserve syntax tree formatting
            blocks = []
            def stash_fenced_code(match):
                code_content = match.group(1).rstrip('\r\n')
                import textwrap
                code_content = textwrap.dedent(code_content)
                code_content_html = code_content.replace('\r\n', '<br>').replace('\n', '<br>')
                idx = len(blocks)
                blocks.append(
                    f'<pre style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; '
                    f'padding:8px 12px; font-family:monospace; font-size:12px; line-height:1.5; '
                    f'white-space:pre-wrap; color:#1e293b; margin:8px 0;">{code_content_html}</pre>'
                )
                return f"__FENCED_CODE_BLOCK_{idx}__"

            h = re.sub(r'[ \t]*```(?:[a-zA-Z0-9_-]+)?[ \t]*\r?\n(.*?)(?:\r?\n[ \t]*```|[ \t]*```)[ \t]*', stash_fenced_code, h, flags=re.DOTALL)
            h = re.sub(r'###\s+(.*?)(?:\n|$)', r'<h4 style="color:#4f46e5; margin:10px 0 4px 0;">\1</h4>', h)
            h = re.sub(r'##\s+(.*?)(?:\n|$)', r'<h3 style="color:#1e293b; margin:12px 0 6px 0;">\1</h3>', h)
            h = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', h)
            h = re.sub(r'`(.*?)`', r'<code style="background:#f1f5f9; color:#e11d48; padding:2px 4px; border-radius:3px;">\1</code>', h)
            h = h.replace("\n", "<br>")

            for i, blk in enumerate(blocks):
                h = h.replace(f"__FENCED_CODE_BLOCK_{i}__", blk)
            return h

        lines = [
            "#separator:tab",
            "#html:true",
            "#tags column:3"
        ]

        for n in notes:
            sentence = n.get("sentence_text") or n.get("title") or "English Sentence"
            is_mistake = bool(n.get("is_mistake"))
            mistake_type = n.get("mistake_type") or ""

            # Front Field
            front_parts = [
                f'<div style="font-family:sans-serif; font-size:20px; font-weight:600; color:#1e293b; line-height:1.5;">{html_escape(sentence)}</div>'
            ]
            if is_mistake:
                front_parts.append(
                    f'<div style="margin-top:8px; font-size:12px; color:#ef4444; font-weight:bold; background:#fee2e2; display:inline-block; padding:2px 8px; border-radius:4px;">⚠️ {html_escape(mistake_type.upper() or "MISTAKE")} 重点错题复习</div>'
                )
            if n.get("document_title"):
                front_parts.append(
                    f'<div style="margin-top:6px; font-size:12px; color:#64748b;">📚 {html_escape(n["document_title"])} · P{n.get("page_number", "")}</div>'
                )
            front_html = "".join(front_parts).replace("\t", " ").replace("\r\n", " ").replace("\n", " ")

            # Back Field
            back_parts = []
            if n.get("title"):
                back_parts.append(f'<div style="font-size:15px; font-weight:700; color:#4f46e5; margin-bottom:8px;">{html_escape(n["title"])}</div>')

            if n.get("voice_text"):
                back_parts.append(
                    f'<div style="font-size:13px; color:#334155; background:#f8fafc; border-left:3px solid #6366f1; padding:8px 12px; margin-bottom:10px; font-style:italic;">🎙️ <b>外教原音讲解</b>: {html_escape(n["voice_text"])}</div>'
                )

            back_parts.append(
                f'<div style="font-size:14px; line-height:1.6; color:#1e293b;">{md_to_anki_html(n.get("notes_markdown", ""))}</div>'
            )
            back_html = "".join(back_parts).replace("\t", " ").replace("\r\n", " ").replace("\n", " ")

            # Tags
            doc_tag = re.sub(r'[^a-zA-Z0-9_]', '_', n.get("document_id") or "General")
            tag_type = "Mistake" if is_mistake else "Note"
            tags = f"EnglishCoach {doc_tag} {tag_type}"

            lines.append(f"{front_html}\t{back_html}\t{tags}")

        return "\n".join(lines)

