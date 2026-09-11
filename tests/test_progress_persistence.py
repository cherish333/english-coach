import pytest
from src.core.progress import ProgressManager

def test_progress_persistence_and_isolation():
    # 1. Save progress on document A (west_civ) page 38 sentence 5
    ProgressManager.save_progress(
        document_id="west_civ",
        page=38,
        sentence_index=5,
        teaching_style="history"
    )
    p = ProgressManager.get_progress()
    assert p["last_document_id"] == "west_civ"
    assert p["last_page"] == 38
    assert p["last_sentence_index"] == 5
    assert p["teaching_style"] == "history"
    assert p["books"]["west_civ"]["last_page"] == 38
    assert p["books"]["west_civ"]["last_sentence_index"] == 5

    # 2. Switch to document B (vocabulary) without explicit page
    # It should look up vocabulary in document_progress or default to 1, without inheriting 38/5
    ProgressManager.save_progress(
        document_id="vocabulary",
        page=None,
        sentence_index=None
    )
    p2 = ProgressManager.get_progress()
    assert p2["last_document_id"] == "vocabulary"
    # vocabulary has its own page and sentence, not west_civ's 38 and 5
    assert p2["last_page"] != 38

    # 3. Now save specific progress for vocabulary
    ProgressManager.save_progress(
        document_id="vocabulary",
        page=12,
        sentence_index=3,
        teaching_style="spoken"
    )
    p3 = ProgressManager.get_progress()
    assert p3["last_document_id"] == "vocabulary"
    assert p3["last_page"] == 12
    assert p3["last_sentence_index"] == 3

    # 4. Switch back to west_civ without page/sentence args
    # It should cleanly restore 38 and 5!
    ProgressManager.save_progress(
        document_id="west_civ",
        page=None,
        sentence_index=None
    )
    p4 = ProgressManager.get_progress()
    assert p4["last_document_id"] == "west_civ"
    assert p4["last_page"] == 38
    assert p4["last_sentence_index"] == 5
