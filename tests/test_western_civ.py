import pytest
from starlette.testclient import TestClient
from src.server import app
from src.core.documents import DocumentLibrary
from src.core.llm import TEACHING_STYLES, get_persona_prompt


def test_western_civ_document_spec():
    lib = DocumentLibrary()
    docs = {d["id"]: d for d in lib.list_documents()}
    assert "west_civ" in docs
    spec = docs["west_civ"]
    assert spec["title"] == "Western Civilization: A Brief History (7th Ed.)"
    assert spec["available"] is True
    assert spec["pages"] == 769


def test_western_civ_chapters_and_units():
    lib = DocumentLibrary()
    units = lib.get_units("west_civ")
    assert len(units) == 30
    assert units[0]["unit"] == 1
    assert units[0]["page"] == 38
    assert "The Ancient Near East" in units[0]["title"]

    # Chapter 12 (Renaissance)
    assert units[11]["unit"] == 12
    assert units[11]["page"] == 279
    assert "Renaissance" in units[11]["title"]


def test_western_civ_sentence_extraction_and_dehyphenation():
    lib = DocumentLibrary()
    sentences = lib.get_page_sentences("west_civ", 38)
    assert len(sentences) >= 5
    full_text = " ".join(s["text"] for s in sentences)
    # Check that hyphenation was repaired
    assert "civili- zation" not in full_text
    assert "civilization" in full_text.lower()
    # Check sentence structure
    assert any("William Loftus" in s["text"] for s in sentences)


def test_history_teaching_persona():
    assert "history" in TEACHING_STYLES
    prompt = get_persona_prompt("history")
    assert "西方文明简史" in prompt
    assert "文明历史背景与学说点拨" in prompt
    assert "文史高阶词汇与词源溯源" in prompt
    assert "史学叙事与长难句剖析" in prompt


def test_western_civ_api_endpoints():
    client = TestClient(app)
    # 1. Documents list
    res = client.get("/api/documents")
    assert res.status_code == 200
    doc_ids = [d["id"] for d in res.json()["documents"]]
    assert "west_civ" in doc_ids

    # 2. Units list
    res_units = client.get("/api/documents/west_civ/units")
    assert res_units.status_code == 200
    units_data = res_units.json()["units"]
    assert len(units_data) == 30

    # 3. Sentences endpoint
    res_sents = client.get("/api/documents/west_civ/pages/38/sentences")
    assert res_sents.status_code == 200
    sents = res_sents.json()["sentences"]
    assert len(sents) >= 5


def test_granular_sentence_splitting_on_dense_pages():
    """Verify that dense pages like Table of Contents (page 9) are not merged into 1 giant run-on sentence."""
    lib = DocumentLibrary()
    sents = lib.get_page_sentences("west_civ", 9)
    # Page 9 was previously a single 2377-character monster sentence.
    # It must now be granularly split into many short, digestible items.
    assert len(sents) >= 40
    max_words = max(len(s["text"].split()) for s in sents)
    assert max_words <= 26
    # Check that kerning artifacts like 'Th e' were cleaned
    all_text = " ".join(s["text"] for s in sents)
    assert "Th e " not in all_text
    assert "The " in all_text


def test_zero_omission_multi_column_order_and_chauvet_quote():
    """Verify page 40 extracts the Chauvet Cave quote, preserves multi-column flow, and excludes vector map junk."""
    lib = DocumentLibrary()
    sents = lib.get_page_sentences("west_civ", 40)
    assert len(sents) >= 20

    # 1. Chauvet Cave quote with ellipses
    assert any("moment of ecstasy" in s["text"] for s in sents)
    assert any("These were moments of indescribable madness" in s["text"] for s in sents)

    # 2. Cross-column sentence continuity
    assert any("cultural activity of Paleolithic peoples" in s["text"] for s in sents)

    # 3. Section headings preserved
    assert any("Neolithic Revolution" in s["text"] for s in sents)

    # 4. Map 1.1 caption and focus question
    assert any("MAP 1.1" in s["text"] for s in sents)
    assert any("climate change affect humans and their movements" in s["text"] for s in sents)

    # 5. Vector map noise excluded
    assert not any("90˚" in s["text"] or "60˚" in s["text"] for s in sents)
    assert not any("00,0,0" in s["text"] for s in sents)
    assert not any("Miles" in s["text"] and "200" in s["text"] for s in sents)


def test_zero_omission_headings_and_chronology():
    """Verify page 38 and 39 preserve all chapter outlines, questions, and chronology timeline."""
    lib = DocumentLibrary()

    # Page 38 (Outline and Focus Questions)
    sents_38 = lib.get_page_sentences("west_civ", 38)
    assert any("CHAPTER 1" in s["text"] for s in sents_38)
    assert any("CHAPTER OUTLINE" in s["text"] for s in sents_38)
    assert any("Paleolithic and Neolithic Ages differ" in s["text"] for s in sents_38)
    assert any("Emergence of Civilization" in s["text"] for s in sents_38)
    assert any("Civilization in Mesopotamia" in s["text"] for s in sents_38)
    assert any("Egyptian Civilization" in s["text"] for s in sents_38)
    assert any("CRITICAL THINKING" in s["text"] for s in sents_38)

    # Page 39 (Continuation + Chronology timeline)
    sents_39 = lib.get_page_sentences("west_civ", 39)
    assert any("crucial role in the development of Western civilization" in s["text"] for s in sents_39)
    assert any("The First Humans" in s["text"] for s in sents_39)
    assert any("Hunter-Gatherers of the Old Stone Age" in s["text"] for s in sents_39)
    # Chronology items
    assert any("CHRONOLOGY The First Humans" in s["text"] for s in sents_39)
    assert any("Australopithecines" in s["text"] for s in sents_39)
    assert any("Homo erectus" in s["text"] for s in sents_39)
    assert any("Neanderthals" in s["text"] for s in sents_39)
    assert any("Homo sapiens sapiens" in s["text"] for s in sents_39)


def test_zero_omission_cross_column_sentences_dense_pages():
    """Verify pages 42, 45, 46 cross-column continuity, captions, and tables."""
    lib = DocumentLibrary()

    # Page 42
    sents_42 = lib.get_page_sentences("west_civ", 42)
    assert any("develop armies and to build walled towns and cities" in s["text"] for s in sents_42)
    assert any("The Emergence of Civilization" in s["text"] for s in sents_42)
    assert not any("Ind Ind Indus" in s["text"] for s in sents_42)
    assert not any("Kilom" in s["text"] for s in sents_42)

    # Page 45
    sents_45 = lib.get_page_sentences("west_civ", 45)
    assert any("Royal Standard" in s["text"] for s in sents_45)
    assert any("petition to his king" in s["text"] for s in sents_45)
    assert any("Kingship" in s["text"] for s in sents_45)
    assert any("Economy and Society" in s["text"] for s in sents_45)

    # Page 46
    sents_46 = lib.get_page_sentences("west_civ", 46)
    assert any("TABLE 1.1 Some Semitic Languages" in s["text"] for s in sents_46)
    assert not any("Babylo ylo" in s["text"] for s in sents_46)
    assert not any("Ti T gris" in s["text"] for s in sents_46)
    assert any("Babylon" in s["text"] for s in sents_46)


def test_no_pronunciation_in_prompts():
    """Verify that pronunciation lecturing is banned and replaced with translation and usage."""
    from src.core.llm import SENTENCE_LECTURE_PROMPT, TEACHING_STYLES, COACH_SYSTEM_PROMPT, LECTURE_SYSTEM_PROMPT

    # 1. Check SENTENCE_LECTURE_PROMPT
    assert "绝对不要讲解发音" in SENTENCE_LECTURE_PROMPT
    assert "发音指南" not in SENTENCE_LECTURE_PROMPT
    assert "中文释义" in SENTENCE_LECTURE_PROMPT

    # 2. Check history teaching persona
    history_prompt = TEACHING_STYLES["history"]
    assert "绝对不讲发音" in history_prompt
    assert "发音节奏" not in history_prompt
    assert "典雅译文" in history_prompt

    # 3. Check COACH and LECTURE prompts
    assert "发音提示" not in COACH_SYSTEM_PROMPT
    assert "pronunciation notes" not in LECTURE_SYSTEM_PROMPT


def test_tts_endpoints():
    """Verify GET and POST /api/tts synthesize audio correctly."""
    client = TestClient(app)

    # 1. Valid GET request
    res = client.get("/api/tts?text=Western+Civilization")
    assert res.status_code == 200
    assert res.headers["content-type"] in ["audio/mpeg", "audio/wav"]
    assert len(res.content) > 100

    # 2. Valid POST request
    res_post = client.post("/api/tts", json={"text": "Hello world"})
    assert res_post.status_code == 200
    assert len(res_post.content) > 100

    # 3. Empty text validation
    res_err = client.get("/api/tts?text=  ")
    assert res_err.status_code == 400


@pytest.mark.anyio
async def test_read_only_stream_immediate():
    """Verify that read_only sentence action returns voice audio chunk immediately without LLM delay."""
    from src.core.llm import LlmCoach
    coach = LlmCoach()
    events = []
    async for ev_type, payload in coach.stream_sentence_lecture("The ancient Greeks pioneered democracy.", action="read_only"):
        events.append((ev_type, payload))

    assert len(events) >= 3
    assert events[0][0] == "voice_sentence"
    assert events[0][1] == "The ancient Greeks pioneered democracy."
    assert events[-1][0] == "done"

    # Verify voice has ONLY the target sentence, no lecture monologue
    voice_chunks = [p for t, p in events if t == "voice_sentence"]
    assert len(voice_chunks) == 1
    assert voice_chunks[0] == "The ancient Greeks pioneered democracy."

    # Verify notes are streamed for whiteboard
    notes_chunks = [p for t, p in events if t == "notes_delta"]
    assert len(notes_chunks) >= 1
    all_notes = "".join(notes_chunks)
    assert len(all_notes) > 0


def test_balanced_sentence_splitting_semantic_integrity():
    """Verify that sentences with <= 2 commas and quotes are kept intact and not split into ungrammatical fragments."""
    lib = DocumentLibrary()

    # Page 38: Loftus quote and Warka ruins should be complete sentences
    sents_38 = lib.get_page_sentences("west_civ", 38)
    loftus_sents = [s["text"] for s in sents_38 if "Loftus" in s["text"] or "looming" in s["text"]]
    # The quote should be fully contained within one sentence and have matching quotes
    assert any("looming in solitary grandeur" in s and "He wrote" in s for s in loftus_sents)
    assert any("One of these piles, known to the natives as the mound of Warka" in s["text"] for s in sents_38)

    # Page 39: Homo sapiens sapiens and archaeological/biological information
    sents_39 = lib.get_page_sentences("west_civ", 39)
    # 1. Quoted "(‘‘wise, wise human being’’)" must NOT be sliced in the middle
    assert any("Homo sapiens sapiens (‘‘wise, wise human being’’)" in s["text"] for s in sents_39)
    assert not any(s["text"].endswith("(‘‘wise.") for s in sents_39)
    assert not any(s["text"].startswith("Wise human being’’)") for s in sents_39)

    # 2. "archaeological and, more recently, biological information" must NOT be sliced
    arch_sents = [s["text"] for s in sents_39 if "archaeological" in s["text"]]
    assert len(arch_sents) == 1
    assert "more recently, biological information" in arch_sents[0]
    assert not any(s["text"].endswith("more recently.") for s in sents_39)



