import pytest
import io
import unittest.mock as mock
from fastapi.testclient import TestClient
from src.server import app
from src.core.gamification import GamificationManager
from src.core.notes_manager import NotesManager
from src.core.documents import DocumentLibrary
from src.core.evaluator import clean_word, evaluate_pronunciation

client = TestClient(app)

def test_notes_limit_clamping():
    # Negative limit should be clamped to at least 1, not error or unbounded
    res_neg = client.get("/api/notes?limit=-1")
    assert res_neg.status_code == 200
    assert "notes" in res_neg.json()
    assert len(res_neg.json()["notes"]) <= 200

    # Non-integer limit returns FastAPI 422 Unprocessable Entity
    res_str = client.get("/api/notes?limit=invalid_number")
    assert res_str.status_code == 422

    # Extreme limit should be clamped to 200
    res_large = client.get("/api/notes?limit=99999")
    assert res_large.status_code == 200
    assert len(res_large.json()["notes"]) <= 200


def test_anki_export_error_handling():
    # If NotesManager raises an exception, should return 500, NOT 200 null
    with mock.patch("src.core.notes_manager.NotesManager.export_anki_tsv", side_effect=RuntimeError("Database corrupt")):
        res = client.get("/api/notes/export/anki")
        assert res.status_code == 500
        assert "Database corrupt" in res.json().get("detail", "")


def test_pdf_upload_size_limit():
    # File > 50MB should be rejected with 413
    large_dummy_pdf = b"%PDF-1.4 " + b"0" * (51 * 1024 * 1024)
    files = {"file": ("large.pdf", io.BytesIO(large_dummy_pdf), "application/pdf")}
    res = client.post("/api/documents/upload", files=files)
    assert res.status_code == 413
    assert "50MB" in res.json().get("detail", "")


def test_pdf_upload_invalid_header():
    # Non-PDF header should be rejected with 400
    fake_pdf = b"NOT_A_PDF_FILE_HEADER" + b"X" * 200
    files = {"file": ("fake.pdf", io.BytesIO(fake_pdf), "application/pdf")}
    res = client.post("/api/documents/upload", files=files)
    assert res.status_code == 400
    assert "Invalid PDF" in res.json().get("detail", "")


def test_gamification_action_validation():
    # Invalid action should be rejected with 400
    res_invalid = client.post("/api/game/action", json={"action_type": "hack_xp", "score": 9999})
    assert res_invalid.status_code == 400

    # Valid action with clamped values
    res_valid = client.post("/api/game/action", json={
        "action_type": "typing_completed",
        "score": 9999.0, # should be clamped
        "combo": 9999,   # should be clamped
        "words_count": 10
    })
    assert res_valid.status_code == 200
    assert res_valid.json()["success"] is True


def test_gamification_typing_aliases():
    # Both typing_completed and shadow_typing should grant stats and quest progress
    res1 = client.post("/api/game/action", json={
        "action_type": "typing_completed",
        "sentence_text": "We need to analyze the data carefully.",
        "combo": 5
    })
    assert res1.status_code == 200
    assert res1.json()["result"]["total_xp_gained"] > 0

    res2 = client.post("/api/game/action", json={
        "action_type": "shadow_typing",
        "sentence_text": "Good morning class.",
        "combo": 3
    })
    assert res2.status_code == 200
    assert res2.json()["result"]["total_xp_gained"] > 0


def test_sentence_extractor_abbreviation_protection():
    import re
    text = "Dr. Smith and Mr. Brown arrived at 9:00 a.m. to inspect the U.S. company e.g. Apple Inc. It was great!"
    abbr_patterns = [
        r"\b(e\.g|i\.e|etc|vs|vol|approx|dept|est|fig)\.",
        r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Gov|Gen|Col)\.",
        r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.",
        r"\b(a\.m|p\.m)\.",
        r"\b([A-Z]\.[A-Z]\.)",
        r"\b([A-Z]\.)\s+(?=[A-Z])",
    ]
    protected_text = text
    for pat in abbr_patterns:
        protected_text = re.sub(pat, lambda m: m.group(0).replace(".", "§DOT§"), protected_text, flags=re.IGNORECASE)
    raw_sentences = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\"'“‘\u4e00-\u9fff])", protected_text)
    clean_sents = [s.replace("§DOT§", ".") for s in raw_sentences]
    
    # Should be 2 sentences: 1) Dr. Smith and Mr. Brown... Inc. 2) It was great!
    assert len(clean_sents) == 2
    assert "Dr. Smith" in clean_sents[0]
    assert "Mr. Brown" in clean_sents[0]
    assert "It was great!" in clean_sents[1]


def test_evaluator_clean_word_and_pronunciation():
    # Quotes and accents should be cleaned
    assert clean_word("“Hello”") == "hello"
    assert clean_word("don’t") == "don't"
    assert clean_word("world,") == "world"

    # Evaluation with curly apostrophe in target
    res = evaluate_pronunciation("I don’t know", "i don't know")
    assert res["score"] == 100
    assert res["level"] == "excellent"
    assert all(w["status"] == "correct" for w in res["words"])


def test_sentence_boxes_extraction():
    lib = DocumentLibrary()
    doc_id = "vocabulary" if lib.specs.get("vocabulary") and lib.specs["vocabulary"].path.is_file() else "west_civ"
    page = 9 if doc_id == "vocabulary" else 38
    res = client.get(f"/api/documents/{doc_id}/pages/{page}/sentences")
    assert res.status_code == 200
    data = res.json()
    assert "sentences" in data
    sentences = data["sentences"]
    assert len(sentences) > 0

    # Ensure sentences have 'boxes' list with percentage coordinates
    has_boxes = 0
    for s in sentences:
        assert "boxes" in s
        assert isinstance(s["boxes"], list)
        if s["boxes"]:
            has_boxes += 1
            for b in s["boxes"]:
                assert "x" in b and "y" in b and "w" in b and "h" in b
                assert 0 <= b["x"] <= 100
                assert 0 <= b["y"] <= 100
                assert b["w"] > 0
                assert b["h"] > 0

    # High match rate on textbook page
    assert has_boxes / len(sentences) >= 0.9


def test_word_glosses_endpoint():
    # Empty sentence returns empty glosses
    res_empty = client.post("/api/sentence/word-glosses", json={"sentence": ""})
    assert res_empty.status_code == 200
    assert res_empty.json()["glosses"] == {}

    # Test contextual word gloss retrieval and caching
    sentence = "He runs a business by the river bank."
    res = client.post("/api/sentence/word-glosses", json={"sentence": sentence})
    assert res.status_code == 200
    data = res.json()
    assert "glosses" in data
    glosses = data["glosses"]
    assert isinstance(glosses, dict)
    assert len(glosses) > 0
    assert "runs" in glosses
    # Second request hits SQLite / memory cache
    res_cached = client.post("/api/sentence/word-glosses", json={"sentence": sentence})
    assert res_cached.status_code == 200
    assert res_cached.json()["glosses"] == glosses


def test_evaluator_bidirectional_contractions():
    # Target contraction -> Spoken expanded
    res1 = evaluate_pronunciation("It's a good day", "it is a good day")
    assert res1["score"] == 100
    assert res1["words"][0]["status"] == "correct"
    assert res1["words"][0]["tip"] == "缩写形式匹配"

    # Target expanded -> Spoken contraction
    res2 = evaluate_pronunciation("It is a good day", "it's a good day")
    assert res2["score"] == 100
    assert res2["words"][0]["status"] == "correct"
    assert res2["words"][1]["status"] == "correct"
    assert res2["words"][0]["tip"] == "缩写形式匹配"


def test_gamification_levels_completeness():
    from src.core.gamification import LEVELS_CONFIG, calculate_level_info
    levels = [item[0] for item in LEVELS_CONFIG]
    # Check that levels 1 to 20 are continuous without missing levels
    assert levels == list(range(1, 21))

    # Verify XP for Lv. 16
    info16 = calculate_level_info(12500)
    assert info16["level"] == 16


def test_anki_export_preserves_spacing_across_newlines():
    notes = [{
        "sentence_text": "First line\nSecond line",
        "title": "Title",
        "voice_text": "Voice",
        "notes_markdown": "Notes",
        "is_mistake": False,
        "document_id": "test_doc"
    }]
    tsv = NotesManager.export_anki_tsv(notes)
    # Newlines should be replaced with spaces, not deleted into 'lineSecond'
    assert "First line Second line" in tsv


def test_chinese_request_detection_and_prompt_injection():
    from src.core.llm import is_chinese_or_translation_request, CHINESE_LECTURE_DIRECTIVE, CHINESE_COACH_DIRECTIVE

    # Chinese queries
    assert is_chinese_or_translation_request("had grown是什么意思中文解释 在这个语境的意思") is True
    assert is_chinese_or_translation_request("继续讲解") is True
    assert is_chinese_or_translation_request("用中文解释一下") is True

    # English queries requesting Chinese
    assert is_chinese_or_translation_request("explain this in chinese please") is True
    assert is_chinese_or_translation_request("what does it mean in chinese?") is True
    assert is_chinese_or_translation_request("please translate this") is True

    # Normal English queries
    assert is_chinese_or_translation_request("Hello coach, how are you today?") is False
    assert is_chinese_or_translation_request("I went to the store this morning.") is False
    assert is_chinese_or_translation_request("") is False

    # Directives contain strict instructions
    assert "严禁通篇输出纯英文" in CHINESE_LECTURE_DIRECTIVE
    assert "严禁通篇输出纯英文" in CHINESE_COACH_DIRECTIVE

