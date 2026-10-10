"""Item-level spaced-repetition scheduler (FSRS-inspired, dependency-free).

Why this exists
---------------
The app previously had no scheduler at all.  ``notes.is_mistake`` was a boolean,
the "review" queue was ``ORDER BY id DESC`` (most recently added first, which is
exactly backwards for memory), and "一键净化" deleted the item for +35 XP without
requiring a single successful recall.  ``save_mistake``'s docstring promised
"spaced repetition review" while the code did a plain INSERT.  The only real
scheduling in the product happened after exporting to Anki.

What this module provides
-------------------------
A small, auditable FSRS-style scheduler:

* Per-item memory state: ``difficulty`` (1-10) and ``stability`` (days).
* Retrievability ``R(t) = (1 + F * t / S) ** DECAY`` with ``F = 19/81`` and
  ``DECAY = -0.5`` — the published FSRS-4.5 shape, so intervals land at the
  same places a trained scheduler would.
* An interval solver that targets a configurable retention (default 0.90).
* Lapse handling that demotes an item to ``relearn`` with short steps instead
  of silently deleting it.
* A due queue with interleaving across item kinds, and a workload forecast.

Every entry point takes an explicit ``now`` so tests never depend on the wall
clock.  No new third-party dependency is required.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.core.notes_manager import get_db_connection

logger = logging.getLogger("english_coach.srs")

# --- FSRS-4.5 constants -----------------------------------------------------
DECAY = -0.5
FACTOR = 19.0 / 81.0
TARGET_RETENTION = 0.90
MAX_INTERVAL_DAYS = 365.0 * 2
MIN_STABILITY = 0.1

# Default FSRS-4.5 weights.  A single-user local app cannot train population
# parameters, so the published defaults are the right choice.
W: Tuple[float, ...] = (
    0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031,
    1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755,
)

VALID_RATINGS = (1, 2, 3, 4)  # again / hard / good / easy
RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY = VALID_RATINGS
VALID_KINDS = ("word", "sentence", "collocation", "grammar", "phoneme")
VALID_MODES = ("copy", "cloze", "recall", "dictation", "pronunciation", "manual")

STATE_NEW = "new"
STATE_LEARNING = "learning"
STATE_REVIEW = "review"
STATE_RELEARN = "relearn"

# Function words carry no learnable meaning in isolation and would flood the
# review queue with junk items ("a", "of", "the"), so they are never seeded as
# standalone word items. They can still be reviewed inside a sentence item.
STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been
before being below between both but by can cannot can't could couldn't did didn't do
does doesn't doing don't down during each few for from further had hadn't has hasn't
have haven't having he he'd he'll he's her here here's hers herself him himself his how
how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most
mustn't my myself no nor not of off on once only or other ought our ours ourselves out
over own same shan't she she'd she'll she's should shouldn't so some such than that
that's the their theirs them themselves then there there's these they they'd they'll
they're they've this those through to too under until up very was wasn't we we'd we'll
we're we've were weren't what what's when when's where where's which while who who's
whom why why's with won't would wouldn't you you'd you'll you're you've your yours
yourself yourselves ok okay yes yeah yep nope
""".split())

# Short-term steps (minutes) used while an item is being acquired.
LEARNING_STEPS_MIN = (1.0, 10.0)
RELEARN_STEPS_MIN = (10.0,)
# A word counts as "known" once it is this stable and has this many spaced wins.
KNOWN_STABILITY_DAYS = 21.0
KNOWN_MIN_REPS = 3
KNOWN_MIN_DISTINCT_DAYS = 3


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_srs_db() -> None:
    """Create the review tables. Safe to call repeatedly (idempotent)."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    answer TEXT NOT NULL DEFAULT '',
                    hint TEXT DEFAULT '',
                    context TEXT DEFAULT '',
                    document_id TEXT,
                    page_number INTEGER,
                    sentence_index INTEGER,
                    difficulty REAL NOT NULL DEFAULT 5.0,
                    stability REAL NOT NULL DEFAULT 0.0,
                    state TEXT NOT NULL DEFAULT 'new',
                    due_at TEXT NOT NULL,
                    reps INTEGER NOT NULL DEFAULT 0,
                    lapses INTEGER NOT NULL DEFAULT 0,
                    step_index INTEGER NOT NULL DEFAULT 0,
                    last_rating INTEGER,
                    last_review_at TEXT,
                    correct_streak INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kind, item_key)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_due ON review_item(due_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_state ON review_item(state)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_doc ON review_item(document_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL REFERENCES review_item(id) ON DELETE CASCADE,
                    reviewed_at TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'recall',
                    correct INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    interval_days REAL NOT NULL DEFAULT 0,
                    stability_after REAL,
                    difficulty_after REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_log_item ON review_log(item_id, reviewed_at)")
            # Seeding bookkeeping so a re-seed never duplicates or resurrects
            # items the learner has already retired.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _fmt_ts(moment: datetime.datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def _coerce_now(now: Optional[datetime.datetime]) -> datetime.datetime:
    if now is None:
        return datetime.datetime.now()
    if now.tzinfo is not None:
        return now.replace(tzinfo=None)
    return now


# ---------------------------------------------------------------------------
# Retrievability / stability maths
# ---------------------------------------------------------------------------

def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after ``elapsed_days`` given item ``stability``."""
    if stability <= 0:
        return 0.0
    if elapsed_days <= 0:
        return 1.0
    return float((1.0 + FACTOR * elapsed_days / stability) ** DECAY)


def interval_for_retention(stability: float, retention: float = TARGET_RETENTION) -> float:
    """Days until retrievability decays to ``retention``."""
    if stability <= 0:
        return 0.0
    retention = min(max(retention, 0.5), 0.99)
    try:
        days = (stability / FACTOR) * (retention ** (1.0 / DECAY) - 1.0)
    except (OverflowError, ValueError):
        return MIN_STABILITY
    return max(MIN_STABILITY, min(days, MAX_INTERVAL_DAYS))


def _init_stability(rating: int) -> float:
    return max(MIN_STABILITY, float(W[max(0, min(rating, 4) - 1)]))


def _init_difficulty(rating: int) -> float:
    return min(10.0, max(1.0, float(W[4] - (rating - 3) * W[5])))


def _next_difficulty(difficulty: float, rating: int) -> float:
    delta = -W[6] * (rating - 3)
    damped = difficulty + delta * ((10.0 - difficulty) / 9.0)
    # Mean reversion keeps an item from pinning at the extremes forever.
    reverted = W[7] * _init_difficulty(RATING_GOOD) + (1.0 - W[7]) * damped
    return min(10.0, max(1.0, reverted))


def _next_stability_success(difficulty: float, stability: float, r: float, rating: int) -> float:
    hard_penalty = W[15] if rating == RATING_HARD else 1.0
    easy_bonus = W[16] if rating == RATING_EASY else 1.0
    gain = (
        math.exp(W[8])
        * (11.0 - difficulty)
        * (stability ** -W[9])
        * (math.exp((1.0 - r) * W[10]) - 1.0)
        * hard_penalty
        * easy_bonus
    )
    return max(MIN_STABILITY, stability * (1.0 + gain))


def _next_stability_failure(difficulty: float, stability: float, r: float) -> float:
    value = (
        W[11]
        * (difficulty ** -W[12])
        * (((stability + 1.0) ** W[13]) - 1.0)
        * math.exp((1.0 - r) * W[14])
    )
    return max(MIN_STABILITY, min(value, stability))


def schedule(
    *,
    state: str,
    difficulty: float,
    stability: float,
    step_index: int,
    reps: int,
    lapses: int,
    last_review_at: Optional[str],
    rating: int,
    now: datetime.datetime,
    retention: float = TARGET_RETENTION,
) -> Dict[str, Any]:
    """Pure scheduling function: current memory state + grade -> next state.

    Returns a dict with ``due_at``, ``interval_days``, ``stability``,
    ``difficulty``, ``state``, ``step_index``.  No database access, so this is
    trivially unit-testable.
    """
    if rating not in VALID_RATINGS:
        raise ValueError(f"rating must be one of {VALID_RATINGS}, got {rating!r}")

    now = _coerce_now(now)
    stability = max(0.0, float(stability or 0.0))
    difficulty = min(10.0, max(1.0, float(difficulty or 5.0)))
    state = state if state in (STATE_NEW, STATE_LEARNING, STATE_REVIEW, STATE_RELEARN) else STATE_NEW
    step_index = max(0, int(step_index or 0))

    last = _parse_ts(last_review_at)
    elapsed_days = max(0.0, (now - last).total_seconds() / 86400.0) if last else 0.0
    r = retrievability(elapsed_days, stability) if stability > 0 and last else 0.0

    new_difficulty = difficulty
    new_stability = stability
    interval_days = 0.0
    new_state = state
    new_step = step_index

    if state == STATE_NEW:
        new_difficulty = _init_difficulty(rating)
        new_stability = _init_stability(rating)
        if rating == RATING_AGAIN:
            new_state = STATE_LEARNING
            new_step = 0
            interval_days = LEARNING_STEPS_MIN[0] / 1440.0
        elif rating == RATING_HARD:
            new_state = STATE_LEARNING
            new_step = 0
            interval_days = LEARNING_STEPS_MIN[1] / 1440.0
        elif rating == RATING_GOOD:
            new_state = STATE_LEARNING
            new_step = 1
            interval_days = LEARNING_STEPS_MIN[1] / 1440.0
        else:  # easy graduates immediately
            new_state = STATE_REVIEW
            new_step = 0
            interval_days = interval_for_retention(new_stability, retention)

    elif state in (STATE_LEARNING, STATE_RELEARN):
        steps = LEARNING_STEPS_MIN if state == STATE_LEARNING else RELEARN_STEPS_MIN
        new_difficulty = _next_difficulty(difficulty, rating)
        # A short-term step (minutes) is not a spaced repetition: R is ~1, so
        # the FSRS stability-increase term is legitimately ~0.  Applying it here
        # would freeze stability forever, so intra-session steps keep stability
        # and only a genuine later review is allowed to compound it.
        same_session_step = elapsed_days < (1.0 / 24.0)
        if rating == RATING_AGAIN:
            new_step = 0
            interval_days = steps[0] / 1440.0
            new_stability = max(MIN_STABILITY, new_stability * 0.5) if new_stability else MIN_STABILITY
        else:
            next_step = new_step + 1
            if next_step >= len(steps):
                new_state = STATE_REVIEW
                new_step = 0
                if new_stability <= 0:
                    new_stability = _init_stability(rating)
                elif not same_session_step:
                    # Graduating after a real delay: let stability compound.
                    new_stability = _next_stability_success(new_difficulty, new_stability, r, rating)
                interval_days = max(1.0, interval_for_retention(new_stability, retention))
            else:
                new_step = next_step
                interval_days = steps[new_step] / 1440.0
                if new_stability <= 0:
                    new_stability = _init_stability(rating)

    else:  # STATE_REVIEW
        new_difficulty = _next_difficulty(difficulty, rating)
        if rating == RATING_AGAIN:
            lapses += 1
            new_stability = _next_stability_failure(difficulty, stability, r)
            new_state = STATE_RELEARN
            new_step = 0
            interval_days = RELEARN_STEPS_MIN[0] / 1440.0
        else:
            new_stability = _next_stability_success(new_difficulty, stability, r, rating)
            new_state = STATE_REVIEW
            new_step = 0
            interval_days = interval_for_retention(new_stability, retention)
            if rating == RATING_HARD:
                interval_days = max(1.0, interval_days * 0.8)
            elif rating == RATING_EASY:
                interval_days = interval_days * 1.15

    interval_days = max(0.0, min(interval_days, MAX_INTERVAL_DAYS))
    due_at = now + datetime.timedelta(days=interval_days)

    return {
        "state": new_state,
        "step_index": new_step,
        "difficulty": round(new_difficulty, 4),
        "stability": round(max(MIN_STABILITY, new_stability), 4),
        "interval_days": round(interval_days, 6),
        "due_at": _fmt_ts(due_at),
        "retrievability_before": round(r, 4),
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "key": row["item_key"],
        "prompt": row["prompt"],
        "answer": row["answer"],
        "hint": row["hint"],
        "context": row["context"],
        "document_id": row["document_id"],
        "page_number": row["page_number"],
        "sentence_index": row["sentence_index"],
        "state": row["state"],
        "difficulty": row["difficulty"],
        "stability": row["stability"],
        "due_at": row["due_at"],
        "reps": row["reps"],
        "lapses": row["lapses"],
        "step_index": row["step_index"],
        "last_rating": row["last_rating"],
        "last_review_at": row["last_review_at"],
        "correct_streak": row["correct_streak"],
        "created_at": row["created_at"],
    }


def upsert_item(
    *,
    kind: str,
    item_key: str,
    prompt: str,
    answer: str,
    hint: str = "",
    context: str = "",
    document_id: Optional[str] = None,
    page_number: Optional[int] = None,
    sentence_index: Optional[int] = None,
    now: Optional[datetime.datetime] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """Insert a review item if new. Existing items keep their memory state.

    Returns the item id, or ``None`` when the input is unusable.
    """
    kind = (kind or "").strip().lower()
    item_key = (item_key or "").strip()
    if kind not in VALID_KINDS or not item_key:
        return None
    answer = (answer or "").strip()
    if not answer:
        return None

    moment = _coerce_now(now)
    stamp = _fmt_ts(moment)
    owns_conn = conn is None
    conn = conn or get_db_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO review_item (
                    kind, item_key, prompt, answer, hint, context,
                    document_id, page_number, sentence_index,
                    difficulty, stability, state, due_at, reps, lapses,
                    step_index, correct_streak, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 5.0, 0.0, 'new', ?, 0, 0, 0, 0, ?, ?)
                ON CONFLICT(kind, item_key) DO UPDATE SET
                    prompt = CASE WHEN excluded.prompt != '' THEN excluded.prompt ELSE review_item.prompt END,
                    answer = excluded.answer,
                    hint = CASE WHEN excluded.hint != '' THEN excluded.hint ELSE review_item.hint END,
                    context = CASE WHEN excluded.context != '' THEN excluded.context ELSE review_item.context END,
                    document_id = COALESCE(excluded.document_id, review_item.document_id),
                    page_number = COALESCE(excluded.page_number, review_item.page_number),
                    sentence_index = COALESCE(excluded.sentence_index, review_item.sentence_index)
            """, (
                kind, item_key, (prompt or "").strip(), answer, (hint or "").strip(), (context or "").strip(),
                document_id, page_number, sentence_index, stamp, stamp, stamp,
            ))
            row = conn.execute(
                "SELECT id FROM review_item WHERE kind = ? AND item_key = ?", (kind, item_key)
            ).fetchone()
            return int(row["id"]) if row else None
    finally:
        if owns_conn:
            conn.close()


def get_item(item_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM review_item WHERE id = ?", (item_id,)).fetchone()
        return _row_to_item(row) if row else None
    finally:
        conn.close()


def grade_item(
    item_id: int,
    rating: int,
    *,
    mode: str = "recall",
    latency_ms: Optional[int] = None,
    now: Optional[datetime.datetime] = None,
    retention: float = TARGET_RETENTION,
) -> Optional[Dict[str, Any]]:
    """Apply one graded review and persist the new memory state."""
    if rating not in VALID_RATINGS:
        raise ValueError(f"rating must be one of {VALID_RATINGS}, got {rating!r}")
    mode = mode if mode in VALID_MODES else "recall"
    moment = _coerce_now(now)
    stamp = _fmt_ts(moment)

    conn = get_db_connection()
    try:
        with conn:
            row = conn.execute("SELECT * FROM review_item WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return None

            result = schedule(
                state=row["state"],
                difficulty=row["difficulty"],
                stability=row["stability"],
                step_index=row["step_index"],
                reps=row["reps"],
                lapses=row["lapses"],
                last_review_at=row["last_review_at"],
                rating=rating,
                now=moment,
                retention=retention,
            )

            correct = 1 if rating >= RATING_HARD else 0
            correct_streak = (row["correct_streak"] + 1) if correct else 0
            lapses = row["lapses"] + (1 if rating == RATING_AGAIN else 0)

            conn.execute("""
                UPDATE review_item
                SET state = ?, step_index = ?, difficulty = ?, stability = ?,
                    due_at = ?, reps = ?, lapses = ?, correct_streak = ?,
                    last_rating = ?, last_review_at = ?, updated_at = ?
                WHERE id = ?
            """, (
                result["state"], result["step_index"], result["difficulty"], result["stability"],
                result["due_at"], row["reps"] + 1, lapses, correct_streak,
                rating, stamp, stamp, item_id,
            ))
            conn.execute("""
                INSERT INTO review_log (
                    item_id, reviewed_at, rating, mode, correct, latency_ms,
                    interval_days, stability_after, difficulty_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_id, stamp, rating, mode, correct, latency_ms,
                result["interval_days"], result["stability"], result["difficulty"],
            ))
    finally:
        conn.close()

    updated = get_item(item_id) or {}
    updated["last_result"] = result
    return updated


def due_items(
    *,
    limit: int = 20,
    now: Optional[datetime.datetime] = None,
    kinds: Optional[Sequence[str]] = None,
    document_id: Optional[str] = None,
    include_new: bool = True,
    interleave: bool = True,
) -> List[Dict[str, Any]]:
    """Return the review queue: due items first, then new items, interleaved.

    Interleaving matters pedagogically: blocked practice of one kind feels
    easier but produces worse retention than mixing kinds.
    """
    moment = _coerce_now(now)
    stamp = _fmt_ts(moment)
    limit = max(1, min(int(limit or 20), 200))

    clauses = []
    params: List[Any] = []
    if kinds:
        clean_kinds = [k for k in kinds if k in VALID_KINDS]
        if clean_kinds:
            clauses.append(f"kind IN ({','.join('?' for _ in clean_kinds)})")
            params.extend(clean_kinds)
    if document_id:
        clauses.append("document_id = ?")
        params.append(document_id)
    where = (" AND " + " AND ".join(clauses)) if clauses else ""

    conn = get_db_connection()
    try:
        due_rows = conn.execute(
            f"SELECT * FROM review_item WHERE due_at <= ?{where} ORDER BY due_at ASC, id ASC",
            (stamp, *params),
        ).fetchall()

        new_rows: Iterable[sqlite3.Row] = []
        if include_new:
            new_rows = conn.execute(
                f"SELECT * FROM review_item WHERE state = 'new'{where} ORDER BY id ASC LIMIT ?",
                (*params, limit),
            ).fetchall()

        items: List[Dict[str, Any]] = [_row_to_item(r) for r in due_rows]
        if include_new:
            seen = {i["id"] for i in items}
            for row in new_rows:
                item = _row_to_item(row)
                if item["id"] not in seen:
                    items.append(item)

        if interleave and items:
            # Round-robin across kinds so a session never blocks on one kind.
            buckets: Dict[str, List[Dict[str, Any]]] = {}
            for item in items:
                buckets.setdefault(item["kind"], []).append(item)
            order = sorted(buckets.keys(), key=lambda k: (k != "sentence", k))
            mixed: List[Dict[str, Any]] = []
            while any(buckets.values()):
                for key in order:
                    bucket = buckets.get(key)
                    if bucket:
                        mixed.append(bucket.pop(0))
        else:
            mixed = items

        return mixed[:limit]
    finally:
        conn.close()


def queue_stats(*, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Counts by state plus how many items are due right now."""
    moment = _coerce_now(now)
    stamp = _fmt_ts(moment)
    conn = get_db_connection()
    try:
        by_state = {
            row["state"]: row["c"]
            for row in conn.execute("SELECT state, COUNT(*) AS c FROM review_item GROUP BY state")
        }
        by_kind = {
            row["kind"]: row["c"]
            for row in conn.execute("SELECT kind, COUNT(*) AS c FROM review_item GROUP BY kind")
        }
        due_now = conn.execute(
            "SELECT COUNT(*) AS c FROM review_item WHERE due_at <= ?", (stamp,)
        ).fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) AS c FROM review_item").fetchone()["c"]
        reviewed_today = conn.execute(
            "SELECT COUNT(*) AS c FROM review_log WHERE reviewed_at >= ?",
            (_fmt_ts(moment.replace(hour=0, minute=0, second=0, microsecond=0)),),
        ).fetchone()["c"]
        reviews_total = conn.execute("SELECT COUNT(*) AS c FROM review_log").fetchone()["c"]
        accuracy_row = conn.execute(
            "SELECT AVG(correct) AS a FROM review_log WHERE reviewed_at >= ?",
            (_fmt_ts(moment - datetime.timedelta(days=30)),),
        ).fetchone()
        known = conn.execute(
            "SELECT COUNT(*) AS c FROM review_item WHERE stability >= ? AND correct_streak >= ?",
            (KNOWN_STABILITY_DAYS, KNOWN_MIN_REPS),
        ).fetchone()["c"]
    finally:
        conn.close()

    return {
        "total": total,
        "due_now": due_now,
        "reviewed_today": reviewed_today,
        "reviews_total": reviews_total,
        "accuracy_30d": round(float(accuracy_row["a"]), 4) if accuracy_row and accuracy_row["a"] is not None else None,
        "known_estimate": known,
        "by_state": by_state,
        "by_kind": by_kind,
    }


def forecast(*, days: int = 14, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Project the daily review workload, so a learner can see the load ahead."""
    moment = _coerce_now(now)
    days = max(1, min(int(days or 14), 180))
    counts = [0] * (days + 1)
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT due_at FROM review_item WHERE state != 'new' AND due_at <= ?",
            (_fmt_ts(moment + datetime.timedelta(days=days)),),
        ).fetchall()
    finally:
        conn.close()

    overdue = 0
    for row in rows:
        due = _parse_ts(row["due_at"])
        if due is None:
            continue
        delta_days = (due - moment).total_seconds() / 86400.0
        if delta_days <= 0:
            overdue += 1
        else:
            idx = min(days, int(math.ceil(delta_days)))
            counts[idx] += 1

    out = [{"day_offset": 0, "date": moment.date().isoformat(), "due": overdue, "overdue": True}]
    for offset in range(1, days + 1):
        out.append({
            "day_offset": offset,
            "date": (moment + datetime.timedelta(days=offset)).date().isoformat(),
            "due": counts[offset],
            "overdue": False,
        })
    return out


# ---------------------------------------------------------------------------
# Seeding from data the app already stores
# ---------------------------------------------------------------------------

def _meta_get(key: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT value FROM review_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def _meta_set(key: str, value: str) -> None:
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO review_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    finally:
        conn.close()


def seed_from_mistakes(*, now: Optional[datetime.datetime] = None, limit: int = 1000) -> int:
    """Turn existing mistake notes into reviewable sentence items.

    Only mistakes the learner has *not* already cleansed are seeded, and an
    item's memory state is never reset by re-seeding.
    """
    moment = _coerce_now(now)
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT document_id, page_number, sentence_index, sentence_text, title,
                   mistake_type, notes_markdown
            FROM notes
            WHERE is_mistake = 1 AND sentence_text IS NOT NULL AND TRIM(sentence_text) != ''
            ORDER BY id DESC LIMIT ?
        """, (max(1, int(limit)),)).fetchall()
    finally:
        conn.close()

    added = 0
    write_conn = get_db_connection()
    try:
        for row in rows:
            sentence = (row["sentence_text"] or "").strip()
            if not sentence:
                continue
            item_id = upsert_item(
                kind="sentence",
                item_key=sentence,
                prompt=sentence,
                answer=sentence,
                hint=(row["title"] or "").strip(),
                context=(row["notes_markdown"] or "")[:2000],
                document_id=row["document_id"],
                page_number=row["page_number"],
                sentence_index=row["sentence_index"],
                now=moment,
                conn=write_conn,
            )
            if item_id:
                added += 1
    finally:
        write_conn.close()

    _meta_set("seeded_from_mistakes_at", _fmt_ts(moment))
    return added


def seed_from_typed_words(
    *,
    min_occurrences: int = 1,
    limit: int = 500,
    now: Optional[datetime.datetime] = None,
) -> int:
    """Seed word items from the lifetime typing odometer.

    Words the learner has typed many times are candidates for productive recall
    (spell them / translate them), not for receptive review.
    """
    moment = _coerce_now(now)
    conn = get_db_connection()
    try:
        # Over-fetch and filter stopwords in Python: keeping ~200 literals in the
        # SQL would be unreadable, and the aggregate is already grouped so the
        # result set stays small.
        fetch_limit = max(1, int(limit)) * 3
        rows = conn.execute("""
            SELECT clean_word AS word, COUNT(*) AS n, MAX(document_id) AS document_id,
                   MAX(sentence_index) AS sentence_index
            FROM typed_words_log
            WHERE clean_word IS NOT NULL AND LENGTH(clean_word) >= 4
            GROUP BY clean_word
            HAVING n >= ?
            ORDER BY n DESC, clean_word ASC
            LIMIT ?
        """, (max(1, int(min_occurrences)), fetch_limit)).fetchall()
    finally:
        conn.close()

    added = 0
    write_conn = get_db_connection()
    try:
        for row in rows:
            if added >= max(1, int(limit)):
                break
            word = (row["word"] or "").strip().lower()
            if not word or word in STOPWORDS:
                continue
            item_id = upsert_item(
                kind="word",
                item_key=word,
                prompt=f"拼写并说出这个词：{word}",
                answer=word,
                hint=f"出现 {row['n']} 次",
                context="",
                document_id=row["document_id"],
                sentence_index=row["sentence_index"],
                now=moment,
                conn=write_conn,
            )
            if item_id:
                added += 1
    finally:
        write_conn.close()

    _meta_set("seeded_from_typed_words_at", _fmt_ts(moment))
    return added


def seed_from_glosses(*, limit: int = 300, now: Optional[datetime.datetime] = None) -> int:
    """Seed word items from the contextual gloss cache (word + sense + example)."""
    moment = _coerce_now(now)
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT source_text, glosses_json FROM sentence_word_glosses
            ORDER BY created_at DESC LIMIT ?
        """, (max(1, int(limit)),)).fetchall()
    finally:
        conn.close()

    added = 0
    write_conn = get_db_connection()
    try:
        for row in rows:
            sentence = (row["source_text"] or "").strip()
            try:
                glosses = json.loads(row["glosses_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(glosses, dict):
                continue
            for word, meaning in list(glosses.items())[:12]:
                word = str(word).strip().lower()
                meaning = str(meaning).strip()
                if not word or not meaning:
                    continue
                if word in STOPWORDS or len(word) < 3:
                    continue
                item_id = upsert_item(
                    kind="word",
                    item_key=word,
                    prompt=f"“{word}” 在本句中的意思？",
                    answer=meaning,
                    hint="",
                    context=sentence,
                    now=moment,
                    conn=write_conn,
                )
                if item_id:
                    added += 1
    finally:
        write_conn.close()

    _meta_set("seeded_from_glosses_at", _fmt_ts(moment))
    return added


def seed_all(*, now: Optional[datetime.datetime] = None, typed_word_limit: int = 300) -> Dict[str, int]:
    """Seed every available source. Idempotent."""
    return {
        "sentences_from_mistakes": seed_from_mistakes(now=now),
        "words_from_glosses": seed_from_glosses(now=now),
        "words_from_typing": seed_from_typed_words(limit=typed_word_limit, now=now),
    }


def mastery_summary(*, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Known / learning / new distribution — an honest ability proxy.

    Unlike the XP level and the "words typed" odometer, this number *falls*
    when memory decays, because it is derived from scheduled state.
    """
    moment = _coerce_now(now)
    stamp = _fmt_ts(moment)
    conn = get_db_connection()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM review_item").fetchone()["c"]
        known = conn.execute("""
            SELECT COUNT(*) AS c FROM review_item
            WHERE stability >= ? AND correct_streak >= ? AND state = 'review'
        """, (KNOWN_STABILITY_DAYS, KNOWN_MIN_REPS)).fetchone()["c"]
        at_risk = conn.execute("""
            SELECT COUNT(*) AS c FROM review_item
            WHERE state IN ('review', 'relearn') AND due_at <= ?
        """, (stamp,)).fetchone()["c"]
        learning = conn.execute(
            "SELECT COUNT(*) AS c FROM review_item WHERE state IN ('learning', 'relearn')"
        ).fetchone()["c"]
        new_count = conn.execute(
            "SELECT COUNT(*) AS c FROM review_item WHERE state = 'new'"
        ).fetchone()["c"]
        per_doc = [
            {"document_id": row["document_id"], "total": row["c"]}
            for row in conn.execute("""
                SELECT document_id, COUNT(*) AS c FROM review_item
                WHERE document_id IS NOT NULL GROUP BY document_id ORDER BY c DESC LIMIT 50
            """)
        ]
    finally:
        conn.close()

    return {
        "total_items": total,
        "known": known,
        "at_risk": at_risk,
        "learning": learning,
        "new": new_count,
        "known_ratio": round(known / total, 4) if total else 0.0,
        "by_document": per_doc,
        "as_of": stamp,
    }


init_srs_db()
