"""Tests for phonetic comparison and the upgraded pronunciation grader.

The central behaviour under test: the grader must reject real minimal-pair
phoneme errors while accepting the legitimate variation a learner produces when
they speak naturally.
"""

import pytest

from src.core.evaluator import evaluate_pronunciation as grade, grade_dictation
from src.core.phonetics import (
    clean_word,
    detect_l1_error,
    l1_error_profile,
    phonetic_key,
    words_phonetically_equal,
)


# ---------------------------------------------------------------------------
# clean_word / tokenisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("world,", "world"),
    ("(hello)", "hello"),
    ('"Yes!"', "yes"),
    ("don\u2019t", "don't"),
    ("is?", "is"),
    ("well-known", "well-known"),
])
def test_clean_word(raw, expected):
    assert clean_word(raw) == expected


# ---------------------------------------------------------------------------
# Phonetic keys distinguish minimal pairs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("think", "sink"),
    ("three", "free"),
    ("thin", "sin"),
    ("live", "leave"),
    ("ship", "sheep"),
    ("very", "wary"),
    ("light", "night"),
    ("rice", "lice"),
    ("sin", "sing"),
])
def test_minimal_pairs_are_phonetically_distinct(a, b):
    assert not words_phonetically_equal(a, b), f"{a}/{b} must not be treated as equal"
    assert detect_l1_error(a, b) is not None, f"{a}/{b} should be a named L1 pattern"


@pytest.mark.parametrize("a,b", [
    ("to", "two"),
    ("their", "there"),
    ("weather", "whether"),
    ("know", "no"),
    ("right", "write"),
    ("accommodate", "acommodate"),
    ("necessary", "neccessary"),
    ("definitely", "definately"),
    ("running", "runing"),
])
def test_equivalent_forms_are_accepted(a, b):
    assert words_phonetically_equal(a, b), f"{a}/{b} should be interchangeable"


# ---------------------------------------------------------------------------
# Pronunciation grading: rejects real errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target,spoken,pattern", [
    ("I think this is the right thing to do", "i think this is the right sing to do", "th_to_s"),
    ("Very well, thank you", "very well sank you", "th_to_s"),
    ("I have three brothers", "i have free brothers", "th_to_f"),
    ("It is a very long journey", "it is a wary long journey", "v_to_w"),
])
def test_phoneme_errors_are_detected_and_penalised(target, spoken, pattern):
    result = grade(target, spoken)
    assert result["score"] < 100, f"{spoken!r} must not score perfectly"
    assert pattern in {p["pattern"] for p in result["error_patterns"]}


def test_a_wrong_word_is_not_reported_as_excellent():
    """Regression: 'think'/'sink' scored 96 before the phonetic grader."""
    result = grade("I think so", "I sink so")
    assert result["level"] != "excellent"
    assert result["score"] < 90


def test_severe_errors_cap_the_score():
    result = grade("I think this is the right thing to do",
                   "i think this is the right sing to do")
    assert result["score"] <= 90


# ---------------------------------------------------------------------------
# Pronunciation grading: accepts correct natural speech
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target,spoken", [
    ("It's a good day", "it is a good day"),
    ("It is a good day", "it's a good day"),
    ("I don\u2019t know", "i don't know"),
    ("The weather is really nice today.", "the weather is really nice today"),
    ("She sells seashells by the seashore.", "she sells seashells by the seashore"),
    # Connected-speech reductions are better English, not errors.
    ("I want to go there.", "i wanna go there"),
    ("I am going to leave.", "i'm gonna leave"),
    ("I have got to go.", "i gotta go"),
    ("It is kind of late.", "it's kinda late"),
    ("Give me a break.", "gimme a break"),
    ("Because it is late.", "cuz it's late"),
])
def test_natural_speech_scores_full_marks(target, spoken):
    result = grade(target, spoken)
    assert result["score"] == 100, f"{spoken!r} should be accepted, got {result['score']}"
    assert result["level"] == "excellent"
    assert not result["error_patterns"]


def test_contraction_tip_is_reported():
    """Existing contract: contraction matches surface a specific tip."""
    result = grade("It's a good day", "it is a good day")
    assert result["words"][0]["status"] == "correct"
    assert result["words"][0]["tip"] == "缩写形式匹配"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_inputs_are_handled():
    assert grade("", "anything")["score"] == 0
    assert grade("   ", "")["score"] == 0
    result = grade("I like it.", "")
    assert result["score"] == 0
    assert all(w["status"] == "missing" for w in result["words"])


def test_stats_are_internally_consistent():
    result = grade("The quick brown fox jumps.", "the quick brown fox jumps")
    stats = result["stats"]
    assert sum(stats[k] for k in ("correct", "imprecise", "accent", "missing")) == stats["total_words"]
    assert 0 <= result["score"] <= 100
    assert result["grade"] in (1, 2, 3, 4)


def test_error_profile_aggregates_by_pattern():
    profile = l1_error_profile([("think", "sink"), ("thin", "sin"), ("three", "free")])
    by_pattern = {entry["pattern"]: entry for entry in profile}
    assert by_pattern["th_to_s"]["count"] == 2
    assert by_pattern["th_to_f"]["count"] == 1
    # Severity ordering puts the meaning-changing patterns first.
    assert profile[0]["severity"] == 1.0


# ---------------------------------------------------------------------------
# Dictation grading
# ---------------------------------------------------------------------------

def test_dictation_grades_exactly_and_loosely():
    assert grade_dictation("Hold the line.", "Hold the line.")["score"] == 100
    # Trailing punctuation and case are not content errors.
    assert grade_dictation("Hold the line.", "hold the line")["score"] == 100


@pytest.mark.parametrize("target,typed,kind", [
    ("I have been learning.", "I have learning.", "omission"),
    ("I like it a lot.", "I like it a lot now.", "extra"),
    ("She received it.", "She recieved it.", "transposition"),
    ("I said hello.", "I said yellow.", "substitution"),
])
def test_dictation_error_taxonomy(target, typed, kind):
    result = grade_dictation(target, typed)
    assert kind in {e["type"] for e in result["errors"]}, result["errors"]


def test_dictation_rating_tracks_accuracy():
    assert grade_dictation("one two three four five", "one two three four five")["grade"] == 4
    assert grade_dictation("one two three four five", "")["grade"] == 1
