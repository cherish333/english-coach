"""Regression tests for the spaced-repetition scheduler.

Every call takes an explicit ``now``; nothing here depends on the wall clock.
"""

import datetime

import pytest

from src.core import srs


BASE = datetime.datetime(2026, 1, 1, 9, 0, 0)


def _schedule(**overrides):
    params = dict(
        state=srs.STATE_NEW,
        difficulty=5.0,
        stability=0.0,
        step_index=0,
        reps=0,
        lapses=0,
        last_review_at=None,
        rating=srs.RATING_GOOD,
        now=BASE,
    )
    params.update(overrides)
    return srs.schedule(**params)


# ---------------------------------------------------------------------------
# Retrievability / interval maths
# ---------------------------------------------------------------------------

def test_retrievability_is_one_at_zero_elapsed_and_decays():
    assert srs.retrievability(0, 10.0) == 1.0
    assert 0.88 < srs.retrievability(10, 10.0) < 0.92
    assert srs.retrievability(30, 10.0) < srs.retrievability(10, 10.0)
    # Zero/negative stability cannot be retrieved.
    assert srs.retrievability(5, 0) == 0.0


def test_interval_solver_hits_the_target_retention():
    for stability in (1.0, 3.7, 13.8, 100.0):
        interval = srs.interval_for_retention(stability, 0.90)
        assert abs(srs.retrievability(interval, stability) - 0.90) < 0.005


@pytest.mark.parametrize("rating", srs.VALID_RATINGS)
def test_new_item_rating_produces_a_sane_first_interval(rating):
    result = _schedule(rating=rating)
    assert result["state"] in (srs.STATE_LEARNING, srs.STATE_REVIEW)
    assert 0 < result["interval_days"] <= srs.MAX_INTERVAL_DAYS
    assert 1.0 <= result["difficulty"] <= 10.0
    assert result["stability"] > 0


def test_invalid_rating_is_rejected():
    with pytest.raises(ValueError):
        _schedule(rating=99)


# ---------------------------------------------------------------------------
# Lifecycle behaviour
# ---------------------------------------------------------------------------

def _walk(rating, slip_days=0.0, steps=8):
    """Review one item on schedule (optionally late) and collect intervals."""
    now = BASE
    cur = srs.schedule(
        state=srs.STATE_NEW, difficulty=5.0, stability=0.0, step_index=0, reps=0,
        lapses=0, last_review_at=None, rating=rating, now=now,
    )
    last_review = now
    intervals = []
    for _ in range(steps):
        due = datetime.datetime.fromisoformat(cur["due_at"])
        review_at = due + datetime.timedelta(days=slip_days)
        cur = srs.schedule(
            state=cur["state"], difficulty=cur["difficulty"], stability=cur["stability"],
            step_index=cur["step_index"], reps=1, lapses=0,
            last_review_at=last_review.isoformat(), rating=rating, now=review_at,
        )
        last_review = review_at
        intervals.append(cur["interval_days"])
    return intervals


@pytest.mark.parametrize("rating", [srs.RATING_GOOD, srs.RATING_EASY, srs.RATING_HARD])
def test_intervals_never_shrink_without_a_lapse(rating):
    intervals = _walk(rating)
    assert intervals == sorted(intervals), f"non-monotonic intervals: {intervals}"


def test_good_reviews_compound_stability():
    """Reviewing on time must grow the interval; this is the whole point of SRS."""
    intervals = _walk(srs.RATING_GOOD)
    assert intervals[-1] > intervals[0] * 3, intervals


def test_interval_is_capped():
    intervals = _walk(srs.RATING_EASY, steps=14)
    assert max(intervals) <= srs.MAX_INTERVAL_DAYS


def test_a_lapse_demotes_to_relearn_and_shortens():
    result = _schedule(
        state=srs.STATE_REVIEW, difficulty=5.0, stability=60.0, step_index=0, reps=20,
        last_review_at=(BASE - datetime.timedelta(days=60)).isoformat(),
        rating=srs.RATING_AGAIN,
    )
    assert result["state"] == srs.STATE_RELEARN
    assert result["stability"] < 60.0
    assert result["interval_days"] < 1.0


def test_learning_step_does_not_freeze_stability_forever():
    """A short same-session step must not consume the stability-increase term."""
    first = _schedule(rating=srs.RATING_GOOD)
    assert first["state"] == srs.STATE_LEARNING
    graduated = _schedule(
        state=first["state"], difficulty=first["difficulty"], stability=first["stability"],
        step_index=first["step_index"], reps=1,
        last_review_at=(BASE + datetime.timedelta(minutes=10)).isoformat(),
        rating=srs.RATING_GOOD,
        now=BASE + datetime.timedelta(minutes=10),
    )
    assert graduated["state"] == srs.STATE_REVIEW
    # Graduating must produce a real day-scale interval, not the 10-minute step.
    assert graduated["interval_days"] >= 1.0

    later = _schedule(
        state=graduated["state"], difficulty=graduated["difficulty"],
        stability=graduated["stability"], step_index=0, reps=2,
        last_review_at=(BASE + datetime.timedelta(minutes=10)).isoformat(),
        rating=srs.RATING_GOOD,
        now=BASE + datetime.timedelta(days=2),
    )
    assert later["stability"] > graduated["stability"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_forecast_shape_for_empty_and_populated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_DB_PATH", str(tmp_path / "f.db"))
    srs.init_srs_db()
    projection = srs.forecast(days=5, now=BASE)
    assert len(projection) == 6
    assert projection[0]["overdue"] is True
    assert all(entry["due"] == 0 for entry in projection)


def test_mastery_summary_handles_an_empty_database(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_DB_PATH", str(tmp_path / "m.db"))
    srs.init_srs_db()
    summary = srs.mastery_summary(now=BASE)
    assert summary["total_items"] == 0
    assert summary["known"] == 0
    assert summary["known_ratio"] == 0.0  # no ZeroDivisionError


def test_stopwords_are_excluded_from_word_seeding(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_DB_PATH", str(tmp_path / "s.db"))
    from src.core.notes_manager import get_db_connection
    # Importing gamification creates typed_words_log (its initialiser only runs
    # once per process, so a fresh COACH_DB_PATH needs the table guaranteed).
    import src.core.gamification  # noqa: F401
    from src.core.gamification import init_gamification_db
    init_gamification_db()
    srs.init_srs_db()
    conn = get_db_connection()
    try:
        with conn:
            for word in ("the", "of", "a", "manuscript", "library"):
                conn.execute(
                    "INSERT INTO typed_words_log (word, clean_word, date_str, created_at) "
                    "VALUES (?, ?, '2026-01-01', '2026-01-01 09:00:00')",
                    (word, word),
                )
    finally:
        conn.close()

    srs.seed_from_typed_words(now=BASE)
    conn = get_db_connection()
    try:
        keys = {row["item_key"] for row in conn.execute("SELECT item_key FROM review_item")}
    finally:
        conn.close()
    assert "manuscript" in keys
    assert "library" in keys
    assert not (keys & srs.STOPWORDS), f"stopwords leaked into the queue: {keys & srs.STOPWORDS}"


def test_grading_is_idempotent_when_reseeding(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_DB_PATH", str(tmp_path / "r.db"))
    from src.core.notes_manager import get_db_connection
    import src.core.gamification  # noqa: F401
    from src.core.gamification import init_gamification_db
    init_gamification_db()
    srs.init_srs_db()
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO typed_words_log (word, clean_word, date_str, created_at) "
                "VALUES ('manuscript','manuscript','2026-01-01','2026-01-01 09:00:00')"
            )
    finally:
        conn.close()

    srs.seed_from_typed_words(now=BASE)
    items = srs.due_items(now=BASE, limit=10)
    assert items, "seeding produced no reviewable item"

    graded = srs.grade_item(items[0]["id"], srs.RATING_GOOD, now=BASE)
    assert graded is not None
    assert graded["reps"] == 1
    stability_after_grade = graded["stability"]

    # Re-seeding must not reset the memory state of an existing item.
    srs.seed_from_typed_words(now=BASE + datetime.timedelta(days=1))
    again = srs.get_item(items[0]["id"])
    assert again["reps"] == 1
    assert again["stability"] == stability_after_grade
