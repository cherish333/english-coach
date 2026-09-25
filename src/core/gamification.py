import sqlite3
import datetime
import re
from typing import Dict, Any, List, Optional
from src.core.notes_manager import get_db_connection

LEVELS_CONFIG = [
    (1, 0, "Lv.1 萌新启航", "Novice Explorer"),
    (2, 100, "Lv.2 语音破冰者", "Sound Pioneer"),
    (3, 250, "Lv.3 语感雏形", "Early Speaker"),
    (4, 450, "Lv.4 语流连读者", "Fluent Reader"),
    (5, 700, "Lv.5 节奏掌控者", "Rhythm Master"),
    (6, 1000, "Lv.6 词法探索家", "Lexical Explorer"),
    (7, 1400, "Lv.7 句法解构官", "Syntax Analyst"),
    (8, 1900, "Lv.8 地道表达者", "Natural Communicator"),
    (9, 2500, "Lv.9 连读高手", "Connected Speech Pro"),
    (10, 3200, "Lv.10 英语演说家", "Eloquent Speaker"),
    (11, 4100, "Lv.11 思辨对话家", "Critical Thinker"),
    (12, 5200, "Lv.12 学术研讨官", "Academic Scholar"),
    (13, 6500, "Lv.13 商务谈判手", "Business Negotiator"),
    (14, 8000, "Lv.14 高阶同声演说", "High-tier Orator"),
    (15, 10000, "Lv.15 跨文化使者", "Cultural Ambassador"),
    (16, 12000, "Lv.16 双语传译家", "Bilingual Interpreter"),
    (17, 14000, "Lv.17 精英领航者", "Elite Communicator"),
    (18, 16000, "Lv.18 跨域交流家", "Global Envoy"),
    (19, 18000, "Lv.19 巅峰演说家", "Master Orator"),
    (20, 20000, "Lv.20 殿堂同传官", "Master Interpreter"),
]

def init_gamification_db():
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_gamification (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    streak_days INTEGER DEFAULT 1,
                    last_active_date TEXT,
                    max_combo INTEGER DEFAULT 0,
                    sentences_mastered INTEGER DEFAULT 0,
                    words_typed INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_quests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    quest_date TEXT NOT NULL,
                    quest_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target_count INTEGER NOT NULL,
                    current_count INTEGER DEFAULT 0,
                    xp_reward INTEGER NOT NULL,
                    is_completed INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_quests_date ON daily_quests(quest_date)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS typed_words_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL,
                    clean_word TEXT NOT NULL,
                    date_str TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    document_id TEXT,
                    sentence_index INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_typed_words_date ON typed_words_log(date_str)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_typed_words_clean ON typed_words_log(clean_word)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_typed_words_created ON typed_words_log(created_at DESC)")

            # Defensive migration for user_gamification columns
            cursor = conn.execute("PRAGMA table_info(user_gamification)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            if "words_typed" not in existing_cols:
                conn.execute("ALTER TABLE user_gamification ADD COLUMN words_typed INTEGER DEFAULT 0")

            # Initialize single user profile row if not exists
            row = conn.execute("SELECT id FROM user_gamification WHERE id = 1").fetchone()
            if not row:
                today_str = datetime.date.today().isoformat()
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("""
                    INSERT INTO user_gamification (
                        id, xp, level, streak_days, last_active_date,
                        max_combo, sentences_mastered, words_typed, updated_at
                    ) VALUES (1, 0, 1, 1, ?, 0, 0, 0, ?)
                """, (today_str, now_str))
    finally:
        conn.close()

# Auto-initialize on import
init_gamification_db()


def calculate_level_info(xp: int) -> Dict[str, Any]:
    level = 1
    title = LEVELS_CONFIG[0][2]
    subtitle = LEVELS_CONFIG[0][3]
    base_xp = 0
    next_xp = LEVELS_CONFIG[1][1]

    for i in range(len(LEVELS_CONFIG)):
        lvl, req_xp, t, sub = LEVELS_CONFIG[i]
        if xp >= req_xp:
            level = lvl
            title = t
            subtitle = sub
            base_xp = req_xp
            if i + 1 < len(LEVELS_CONFIG):
                next_xp = LEVELS_CONFIG[i + 1][1]
            else:
                next_xp = req_xp + 5000
        else:
            break

    span = max(1, next_xp - base_xp)
    progress_pct = min(100, max(0, int(((xp - base_xp) / span) * 100)))

    return {
        "level": level,
        "title": title,
        "subtitle": subtitle,
        "xp": xp,
        "current_level_base_xp": base_xp,
        "next_level_xp": next_xp,
        "progress_percent": progress_pct
    }


class GamificationManager:
    @staticmethod
    def get_status() -> Dict[str, Any]:
        today_str = datetime.date.today().isoformat()
        conn = get_db_connection()
        try:
            # 1. User stats
            user_row = conn.execute("SELECT * FROM user_gamification WHERE id = 1").fetchone()
            if not user_row:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO user_gamification (
                            id, xp, level, streak_days, last_active_date,
                            max_combo, sentences_mastered, words_typed, updated_at
                        ) VALUES (1, 0, 1, 1, ?, 0, 0, 0, ?)
                    """, (today_str, now_str))
                user_row = conn.execute("SELECT * FROM user_gamification WHERE id = 1").fetchone()

            user_dict = dict(user_row)
            level_info = calculate_level_info(user_dict["xp"])

            # 2. Daily Quests for today
            GamificationManager._ensure_daily_quests(conn, today_str)
            quests_rows = conn.execute("""
                SELECT * FROM daily_quests WHERE quest_date = ? ORDER BY id ASC
            """, (today_str,)).fetchall()
            quests = [dict(q) for q in quests_rows]

            # 3. Mistakes count
            mistakes_count_row = conn.execute("SELECT COUNT(*) as c FROM notes WHERE is_mistake = 1").fetchone()
            mistakes_count = mistakes_count_row["c"] if mistakes_count_row else 0

            # 4. Odometer today & unique words
            today_words_row = conn.execute(
                "SELECT COUNT(*) as c FROM typed_words_log WHERE date_str = ?", (today_str,)
            ).fetchone()
            today_words = today_words_row["c"] if today_words_row else 0

            unique_words_row = conn.execute(
                "SELECT COUNT(DISTINCT clean_word) as c FROM typed_words_log"
            ).fetchone()
            unique_words = unique_words_row["c"] if unique_words_row else 0

            # Calculate active streak (0 if user missed yesterday and today)
            streak_days = user_dict["streak_days"]
            last_date = user_dict.get("last_active_date")
            yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            if last_date and last_date != today_str and last_date != yesterday_str:
                streak_days = 0
        finally:
            conn.close()

        return {
            "xp": user_dict["xp"],
            "level": level_info["level"],
            "title": level_info["title"],
            "subtitle": level_info["subtitle"],
            "progress_percent": level_info["progress_percent"],
            "current_level_base_xp": level_info["current_level_base_xp"],
            "next_level_xp": level_info["next_level_xp"],
            "streak_days": streak_days,
            "max_combo": user_dict["max_combo"],
            "sentences_mastered": user_dict["sentences_mastered"],
            "words_typed": user_dict["words_typed"],
            "today_words": today_words,
            "unique_words": unique_words,
            "quests": quests,
            "mistakes_count": mistakes_count
        }

    @staticmethod
    def _ensure_daily_quests(conn: sqlite3.Connection, date_str: str):
        row = conn.execute("SELECT COUNT(*) as c FROM daily_quests WHERE quest_date = ?", (date_str,)).fetchone()
        if not row or row["c"] == 0:
            defaults = [
                ("typing", "✍️ 完成 5 句键盘对照跟打", 5, 50),
                ("pronunciation", "🎙️ 发音评测达到 85 分以上 2 次", 2, 60),
                ("dialogue", "💬 与 AI 外教口语互动交流 2 次", 2, 45)
            ]
            with conn:
                for q_type, q_title, target, reward in defaults:
                    conn.execute("""
                        INSERT INTO daily_quests (quest_date, quest_type, title, target_count, current_count, xp_reward, is_completed)
                        VALUES (?, ?, ?, ?, 0, ?, 0)
                    """, (date_str, q_type, q_title, target, reward))

    @staticmethod
    def record_words_typed(
        words: List[str],
        document_id: Optional[str] = None,
        sentence_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """Record individual typed words, updating lifetime odometer and persistent word log."""
        cleaned_words = []
        if words:
            for w in words:
                if not isinstance(w, str):
                    continue
                clean = re.sub(r"[^a-zA-Z0-9'’\-]", "", w).strip()
                if clean:
                    cleaned_words.append((w.strip(), clean))

        today_str = datetime.date.today().isoformat()
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        try:
            with conn:
                user_row = conn.execute("SELECT * FROM user_gamification WHERE id = 1").fetchone()
                if not user_row:
                    conn.execute("""
                        INSERT OR IGNORE INTO user_gamification (
                            id, xp, level, streak_days, last_active_date,
                            max_combo, sentences_mastered, words_typed, updated_at
                        ) VALUES (1, 0, 1, 1, ?, 0, 0, 0, ?)
                    """, (today_str, now_str))
                    user_row = conn.execute("SELECT * FROM user_gamification WHERE id = 1").fetchone()

                count = len(cleaned_words)
                curr_words = user_row["words_typed"] + count
                streak = user_row["streak_days"]
                last_date = user_row["last_active_date"]

                if count > 0:
                    if last_date == yesterday_str:
                        streak += 1
                    elif last_date != today_str:
                        streak = 1

                    conn.execute("""
                        UPDATE user_gamification
                        SET words_typed = ?, streak_days = ?, last_active_date = ?, updated_at = ?
                        WHERE id = 1
                    """, (curr_words, streak, today_str, now_str))

                    for raw, clean in cleaned_words:
                        conn.execute("""
                            INSERT INTO typed_words_log (
                                word, clean_word, date_str, created_at, document_id, sentence_index
                            ) VALUES (?, ?, ?, ?, ?, ?)
                        """, (raw, clean, today_str, now_str, document_id, sentence_index))

                today_words_row = conn.execute(
                    "SELECT COUNT(*) as c FROM typed_words_log WHERE date_str = ?", (today_str,)
                ).fetchone()
                today_words = today_words_row["c"] if today_words_row else 0

                unique_words_row = conn.execute(
                    "SELECT COUNT(DISTINCT clean_word) as c FROM typed_words_log"
                ).fetchone()
                unique_words = unique_words_row["c"] if unique_words_row else 0
        finally:
            conn.close()

        return {
            "success": True,
            "words_recorded": count,
            "total_words": curr_words,
            "today_words": today_words,
            "unique_words": unique_words
        }

    @staticmethod
    def get_odometer_stats() -> Dict[str, Any]:
        """Fetch odometer metrics: lifetime total words, today's words, unique vocabulary, recent and top words."""
        today_str = datetime.date.today().isoformat()
        conn = get_db_connection()
        try:
            user_row = conn.execute("SELECT words_typed, max_combo FROM user_gamification WHERE id = 1").fetchone()
            total_words = user_row["words_typed"] if user_row else 0
            max_combo = user_row["max_combo"] if user_row else 0

            today_row = conn.execute(
                "SELECT COUNT(*) as c FROM typed_words_log WHERE date_str = ?", (today_str,)
            ).fetchone()
            today_words = today_row["c"] if today_row else 0

            unique_row = conn.execute(
                "SELECT COUNT(DISTINCT clean_word) as c FROM typed_words_log"
            ).fetchone()
            unique_words = unique_row["c"] if unique_row else 0

            recent_rows = conn.execute("""
                SELECT clean_word, created_at
                FROM typed_words_log
                ORDER BY id DESC
                LIMIT 30
            """).fetchall()
            recent_words = [{"word": r["clean_word"], "created_at": r["created_at"]} for r in recent_rows]

            top_rows = conn.execute("""
                SELECT clean_word, COUNT(*) as count
                FROM typed_words_log
                GROUP BY clean_word
                ORDER BY count DESC, clean_word ASC
                LIMIT 15
            """).fetchall()
            top_words = [{"word": r["clean_word"], "count": r["count"]} for r in top_rows]
        finally:
            conn.close()

        return {
            "total_words": total_words,
            "today_words": today_words,
            "unique_words": unique_words,
            "max_combo": max_combo,
            "recent_words": recent_words,
            "top_words": top_words
        }

    @staticmethod
    def record_action(
        action_type: str,
        score: float = 0.0,
        words_count: int = 0,
        combo: int = 0,
        increment_words: bool = True,
        words_list: Optional[List[str]] = None,
        document_id: Optional[str] = None,
        sentence_index: Optional[int] = None,
        sentence_text: Optional[str] = None
    ) -> Dict[str, Any]:
        today_str = datetime.date.today().isoformat()
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        try:
            with conn:
                user_row = conn.execute("SELECT * FROM user_gamification WHERE id = 1").fetchone()
                if not user_row:
                    conn.execute("""
                        INSERT OR IGNORE INTO user_gamification (
                            id, xp, level, streak_days, last_active_date,
                            max_combo, sentences_mastered, words_typed, updated_at
                        ) VALUES (1, 0, 1, 1, ?, 0, 0, 0, ?)
                    """, (today_str, now_str))
                    user_row = conn.execute("SELECT * FROM user_gamification WHERE id = 1").fetchone()

                curr_xp = user_row["xp"]
                curr_lvl = user_row["level"]
                streak = user_row["streak_days"]
                last_date = user_row["last_active_date"]
                curr_max_combo = user_row["max_combo"]
                mastered = user_row["sentences_mastered"]
                typed_words = user_row["words_typed"]

                # 1. Update streak
                if last_date == yesterday_str:
                    streak += 1
                elif last_date != today_str:
                    streak = 1

                # 2. Calculate XP gained for this action
                xp_gained = 0
                is_typing = action_type in ("typing_completed", "shadow_typing", "word_typed")
                if action_type == "word_typed":
                    xp_gained = max(1, words_count)
                    if increment_words:
                        typed_words += words_count
                    w_items = words_list or ([sentence_text] if sentence_text else [])
                    for raw in w_items:
                        clean = re.sub(r"[^a-zA-Z0-9'’\-]", "", raw).strip()
                        if clean:
                            conn.execute("""
                                INSERT INTO typed_words_log (
                                    word, clean_word, date_str, created_at, document_id, sentence_index
                                ) VALUES (?, ?, ?, ?, ?, ?)
                            """, (raw.strip(), clean, today_str, now_str, document_id, sentence_index))
                elif is_typing:
                    combo_bonus = min(combo, 25)
                    words_bonus = max(1, words_count // 3)
                    xp_gained = 15 + combo_bonus + words_bonus
                    mastered += 1
                    if increment_words:
                        typed_words += words_count
                        # Log words to typed_words_log if available
                        w_items = words_list
                        if not w_items and sentence_text:
                            w_items = re.findall(r"[a-zA-Z0-9]+(?:['’\-][a-zA-Z0-9]+)*", sentence_text)
                        if w_items:
                            for raw in w_items:
                                clean = re.sub(r"[^a-zA-Z0-9'’\-]", "", raw).strip()
                                if clean:
                                    conn.execute("""
                                        INSERT INTO typed_words_log (
                                            word, clean_word, date_str, created_at, document_id, sentence_index
                                        ) VALUES (?, ?, ?, ?, ?, ?)
                                    """, (raw.strip(), clean, today_str, now_str, document_id, sentence_index))
                    if combo > curr_max_combo:
                        curr_max_combo = combo
                elif action_type in ("pronunciation_evaluated", "pronunciation_s_rank"):
                    if score >= 90:
                        xp_gained = round(score * 0.4) + 12 # S-Rank bonus
                    elif score >= 75:
                        xp_gained = round(score * 0.3)
                    else:
                        xp_gained = 10
                    mastered += 1
                elif action_type == "dialogue_sent":
                    xp_gained = 15
                elif action_type == "sentence_read":
                    xp_gained = 5
                elif action_type == "mistake_cleansed":
                    xp_gained = 35
                else:
                    xp_gained = 10

                # 3. Update Quests & Quest XP
                GamificationManager._ensure_daily_quests(conn, today_str)
                completed_quests = []
                quest_bonus_xp = 0

                quests_rows = conn.execute("SELECT * FROM daily_quests WHERE quest_date = ?", (today_str,)).fetchall()
                for q in quests_rows:
                    q_id = q["id"]
                    q_type = q["quest_type"]
                    target = q["target_count"]
                    current = q["current_count"]
                    is_done = q["is_completed"]

                    should_inc = False
                    if not is_done:
                        if q_type == "typing" and is_typing and action_type != "word_typed":
                            should_inc = True
                        elif q_type == "pronunciation" and action_type in ("pronunciation_evaluated", "pronunciation_s_rank") and score >= 85:
                            should_inc = True
                        elif q_type == "dialogue" and action_type == "dialogue_sent":
                            should_inc = True

                    if should_inc:
                        current += 1
                        if current >= target:
                            is_done = 1
                            quest_bonus_xp += q["xp_reward"]
                            completed_quests.append(q["title"])

                        conn.execute("""
                            UPDATE daily_quests SET current_count = ?, is_completed = ? WHERE id = ?
                        """, (current, is_done, q_id))

                total_gain = xp_gained + quest_bonus_xp
                new_xp = curr_xp + total_gain
                new_level_info = calculate_level_info(new_xp)
                new_level = new_level_info["level"]
                level_up = new_level > curr_lvl

                conn.execute("""
                    UPDATE user_gamification
                    SET xp = ?, level = ?, streak_days = ?, last_active_date = ?,
                        max_combo = ?, sentences_mastered = ?, words_typed = ?, updated_at = ?
                    WHERE id = 1
                """, (new_xp, new_level, streak, today_str, curr_max_combo, mastered, typed_words, now_str))
        finally:
            conn.close()

        status_info = {
            "level": new_level,
            "title": new_level_info["title"],
            "streak_days": streak,
            "progress_percent": new_level_info["progress_percent"],
            "words_typed": typed_words,
            "max_combo": curr_max_combo,
            "sentences_mastered": mastered,
        }

        return {
            "xp_gained": xp_gained,
            "quest_bonus_xp": quest_bonus_xp,
            "total_xp_gained": total_gain,
            "level_up": level_up,
            "level": new_level,
            "title": new_level_info["title"],
            "progress_percent": new_level_info["progress_percent"],
            "streak_days": streak,
            "completed_quests": completed_quests,
            "words_typed": typed_words,
            "status": status_info
        }

    @staticmethod
    def get_mistakes_rush(limit: int = 30) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            rows = conn.execute("""
                SELECT id, document_id, document_title, page_number, sentence_index,
                       sentence_text, title, mistake_type, notes_markdown, created_at
                FROM notes
                WHERE is_mistake = 1
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def cleanse_mistake(note_id: int) -> Dict[str, Any]:
        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.execute("""
                    UPDATE notes SET is_mistake = 0 WHERE id = ? AND is_mistake = 1
                """, (note_id,))
                affected = cursor.rowcount
        finally:
            conn.close()

        if affected > 0:
            reward = GamificationManager.record_action("mistake_cleansed")
            return {"success": True, "note_id": note_id, "reward": reward}
        return {"success": False, "message": "Note not found or already cleansed"}
