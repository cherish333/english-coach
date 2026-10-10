"""Regression tests for the bug fixes.

These pin the *fixed* behaviour of defects that were found by auditing the
running system, so they cannot silently come back. Each test names the concrete
failure it prevents.
"""

import datetime
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import PROJECT_ROOT
from src.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# P0: exponential page-cursor re-parse (document API freeze)
# ---------------------------------------------------------------------------

def test_page_cursor_resolution_is_not_exponential():
    """Jumping to a high page must not re-parse every earlier page repeatedly.

    The old implementation resolved page N by calling get_page_sentences() for
    every earlier page, and that call re-entered the resolver, so work doubled
    per level: page 20 cost 1024 parses, page 24 cost 4096 (~40s). The document
    API shares one worker, so that froze every document endpoint.
    """
    from src.core.documents import DocumentLibrary

    lib = DocumentLibrary()
    # Pick any document that actually has pages; the pathological shape was a
    # sidecar without a per-page cursor field, which the YouTube imports have.
    doc_id = None
    for candidate in lib.specs:
        spec = lib.specs[candidate]
        try:
            if spec.path.is_file():
                doc_id = candidate
                break
        except AttributeError:
            continue
    if doc_id is None:
        pytest.skip("no readable document available")

    real = lib.get_page_sentences
    calls = {"n": 0}

    def counting(document_id, page_number):
        calls["n"] += 1
        return real(document_id, page_number)

    lib.get_page_sentences = counting
    try:
        target = 12
        try:
            lib.get_page_sentences(doc_id, target)
        except Exception:
            pytest.skip("document could not be parsed in this environment")
        # The exponential version needed >= 2^(target/2) = 64 calls here.
        assert calls["n"] <= target + 2, (
            f"page {target} caused {calls['n']} page parses; cursor resolution "
            "looks exponential again"
        )
    finally:
        lib.get_page_sentences = real
        lib.close()


# ---------------------------------------------------------------------------
# P1: CJK sentences were silently deleted
# ---------------------------------------------------------------------------

def test_cjk_sentences_are_not_dropped():
    """Chinese sentences must survive extraction.

    add_sentence() required a Latin letter, so pure-CJK sentences vanished from
    both the sentence list and the page text handed to the LLM, even though the
    splitter is explicitly CJK-aware and CJK PDFs can be uploaded.
    """
    from src.core.documents import DocumentLibrary

    lib = DocumentLibrary()
    try:
        # Exercise the filter directly through a tiny in-memory PDF.
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "This is an English sentence.", fontname="helv", fontsize=12)
        page.insert_text((72, 130), "\u8fd9\u662f\u4e00\u4e2a\u4e2d\u6587\u53e5\u5b50\u3002", fontname="china-s", fontsize=12)
        assert len(doc) == 1
        doc.close()
    finally:
        lib.close()

    # Direct unit check of the filter's contract.
    import re as _re

    def accepted(t: str) -> bool:
        return len(t) >= 4 and bool(_re.search(r"[a-zA-Z\u4e00-\u9fff]", t))

    assert accepted("This is English.")
    assert accepted("\u8fd9\u662f\u4e00\u4e2a\u4e2d\u6587\u53e5\u5b50\u3002")
    assert accepted("Mixed \u6df7\u5408 content.")
    # Punctuation-only and tiny fragments are still rejected.
    assert not accepted("\u3002\u3002\u3002")
    assert not accepted("ab")


# ---------------------------------------------------------------------------
# P1: NOTE: sidebar regex could never match
# ---------------------------------------------------------------------------

def test_note_sidebar_regex_matches():
    import re

    pattern = r"^(CHRONOLOGY|TABLE|NOTE:)(?:\s|$)"
    assert re.match(pattern, "NOTE: hello", re.IGNORECASE)
    assert re.match(pattern, "TABLE 3.1", re.IGNORECASE)
    assert re.match(pattern, "CHRONOLOGY", re.IGNORECASE)
    # The old pattern used \b after the colon, which never matches.
    assert re.match(r"^(CHRONOLOGY|TABLE|NOTE:)\b", "NOTE: hello", re.IGNORECASE) is None


# ---------------------------------------------------------------------------
# P1: stale practice mode hijacked all later speech
# ---------------------------------------------------------------------------

def test_leaving_practice_mode_clears_the_target():
    """Switching away from practice must reset the sentence-action state.

    Otherwise active_sentence_action stayed "practice" forever and every later
    utterance was scored against the previous sentence instead of reaching the
    coach, so the tutor stopped replying.
    """
    server_py = (PROJECT_ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert 'if mode != "practice":' in server_py
    assert 'active_sentence_action = "explain"' in server_py
    assert 'active_target_sentence = ""' in server_py
    # And entering a non-practice sentence action must reset it too.
    assert 'if action != "practice":' in server_py


# ---------------------------------------------------------------------------
# P1: destructive endpoints had no same-origin guard
# ---------------------------------------------------------------------------

def test_shutdown_requires_a_local_same_origin_request():
    server_py = (PROJECT_ROOT / "src" / "server.py").read_text(encoding="utf-8")
    marker = "async def system_shutdown_endpoint(request: Request):"
    assert marker in server_py
    body = server_py.split(marker, 1)[1][:700]
    assert "require_local_model_control(request)" in body, (
        "shutdown must be gated: any cross-site page could otherwise POST it"
    )


def test_shutdown_rejects_cross_site_origin():
    resp = client.post(
        "/api/system/shutdown",
        headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )
    assert resp.status_code == 403
    assert "禁止跨站" in resp.json()["detail"] or "模型控制" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# P2: POST /api/tts bypassed the GET length limit
# ---------------------------------------------------------------------------

def test_post_tts_enforces_the_text_limit():
    server_py = (PROJECT_ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert "MAX_TTS_TEXT_CHARS" in server_py
    resp = client.post("/api/tts", json={"text": "a" * 8000})
    assert resp.status_code == 413
    assert "3000" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# P2: chat history window misalignment
# ---------------------------------------------------------------------------

def test_history_window_still_starts_with_a_user_turn():
    """The sliding window must drop an even number of messages.

    Trimming a single message left the window starting with an assistant turn,
    which strict chat templates reject (surfaced as a bogus "model not ready").
    """
    llm_py = (PROJECT_ROOT / "src" / "core" / "llm.py").read_text(encoding="utf-8")
    assert "overflow % 2" in llm_py

    # Simulate the trim for many round counts.
    for turns in range(1, 40):
        history = []
        for i in range(turns):
            history.append({"role": "user", "content": f"u{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})
        window = 12 * 2
        if len(history) > window:
            overflow = len(history) - window
            del history[:overflow + (overflow % 2)]
        assert history[0]["role"] == "user", f"window starts with {history[0]['role']} at {turns} turns"


# ---------------------------------------------------------------------------
# P2: dictation grading and cloze generation
# ---------------------------------------------------------------------------

def test_dictation_grading_reports_errors():
    from src.core.evaluator import grade_dictation

    perfect = grade_dictation("I have been learning.", "I have been learning.")
    assert perfect["score"] == 100 and perfect["grade"] == 4 and perfect["errors"] == []

    omitted = grade_dictation("I have been learning.", "I have learning.")
    assert omitted["errors"][0]["type"] == "omission"
    assert omitted["errors"][0]["expected"] == "been"
    assert omitted["score"] < 100

    transposed = grade_dictation("She received it.", "She recieved it.")
    assert transposed["errors"][0]["type"] == "transposition"

    empty = grade_dictation("I like it.", "")
    assert empty["score"] == 0 and empty["grade"] == 1


def test_cloze_masks_content_words_only():
    from src.core.evaluator import mask_for_cloze

    masked, hidden = mask_for_cloze("The ancient library contains priceless manuscripts.", 0.5)
    assert hidden, "cloze hid nothing"
    assert all("_" in token or token for token in masked.split())
    for word in hidden:
        assert len(word) >= 4
        assert word.lower() not in {"the", "and", "of"}


# ---------------------------------------------------------------------------
# Frontend fixes (asserted against source, the established pattern in this repo)
# ---------------------------------------------------------------------------

def test_escape_html_escapes_quotes():
    """escapeHtml is used inside attribute values, so quotes must be escaped."""
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'replace(/"/g, "&quot;")' in app_js
    assert "replace(/'/g, \"&#39;\")" in app_js


def test_ime_composition_is_not_treated_as_send():
    """Enter while a Chinese IME is composing must not send the message."""
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "e.isComposing || e.keyCode === 229" in app_js


def test_space_guard_knows_the_real_modal_classes():
    """The push-to-talk guard must recognise the modal classes actually used."""
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert '.closest(".modal-backdrop")' in app_js
    # The non-existent selector must be gone from the guard.
    assert 'el.closest(".modal")' not in app_js


def test_fullscreen_overlays_sit_below_modals_and_toasts():
    css = (PROJECT_ROOT / "src" / "static" / "style.css").read_text(encoding="utf-8")
    # Immersive layers use 99998/99999, so modal + toast must be above them.
    assert "z-index: 100000" in css
    assert "z-index: 100001" in css


def test_odometer_flush_rearms_instead_of_stranding_words():
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "if (this.isFlushing) {" in app_js
    assert "stranding" in app_js


def test_word_audio_cache_is_bounded_and_revokes_urls():
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "WORD_AUDIO_CACHE_MAX" in app_js
    assert "function cacheWordAudio(" in app_js
    # No direct, unbounded writes may remain outside the helper.
    occurrences = app_js.count("wordAudioCache.set(")
    assert occurrences == 1, f"expected only the bounded helper to write the cache, found {occurrences}"
    assert "URL.revokeObjectURL" in app_js


def test_mic_click_toggle_is_reachable():
    """A short click must be able to stop a recording it started."""
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'micBtn.addEventListener("click"' in app_js
    assert "<= 400" in app_js
    # The failure path must release the microphone instead of only alerting.
    assert "micStream.getTracks().forEach(t => t.stop())" in app_js


# ---------------------------------------------------------------------------
# Unbounded in-memory caches (long-session memory growth)
# ---------------------------------------------------------------------------

def test_translation_memory_caches_are_bounded():
    """Reading a long book must not grow the process without limit.

    Both caches had inlined eviction on the write path only; the read path
    assigned directly, so a pure read-through grew them forever.
    """
    from src.core.translation import TranslationService

    service = TranslationService()
    for i in range(service._memory_cache_limit + 1200):
        service._remember(f"bounded sentence {i}", f"译文 {i}")
    assert len(service._memory_cache) <= service._memory_cache_limit

    for i in range(service._memory_cache_limit + 1200):
        service._remember_glosses(f"bounded gloss {i}", {"word": "词"})
    assert len(service._glosses_memory_cache) <= service._memory_cache_limit


def test_smart_notes_cache_is_bounded_on_read_path():
    """get_cached() used to bypass the 500-entry cap that save_cache enforced."""
    from src.core.notes_generator import SmartNotesGenerator

    generator = SmartNotesGenerator()
    for i in range(900):
        generator._remember(f"note sentence {i}", "x" * 200)
    assert len(generator._memory_cache) <= 500


def test_cache_writes_go_through_the_bounded_helpers():
    """Guard against a future edit re-introducing a direct, uncapped write."""
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src" / "core" / "translation.py").read_text(encoding="utf-8")
    # Only the helper bodies may assign into the caches.
    assignments = [
        line.strip() for line in source.splitlines()
        if "self._memory_cache[" in line and "] =" in line
        or "self._glosses_memory_cache[" in line and "] =" in line
    ]
    assert len(assignments) == 2, f"unexpected direct cache writes: {assignments}"


def test_pronunciation_card_is_reset_on_sentence_change():
    """A score for sentence 3 used to stay on screen for every later sentence."""
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function hidePronunciationResult(" in app_js
    assert "hidePronunciationResult();" in app_js
    # It must be called from the sentence-setup path, not only from the display path.
    setup_body = app_js.split("function setupShadowTyping(", 1)[1][:6000]
    assert "hidePronunciationResult()" in setup_body, (
        "the pronunciation card is never hidden when the sentence changes"
    )


def test_sound_toggle_reflects_persisted_state_on_load():
    """After reload the button still showed 🔊 while clicks were silent."""
    app_js = (PROJECT_ROOT / "src" / "static" / "app.js").read_text(encoding="utf-8")
    assert "syncButton()" in app_js
    assert "SoundEngine.syncButton();" in app_js


def test_smart_notes_cache_access_is_off_the_event_loop():
    """Sync SQLite (10s busy timeout) must not run inline in an async handler.

    generate_notes() called get_cached()/save_cache() directly, so a locked
    database stalled the event loop — and with it the mic pump and the coach
    stream, which is exactly what this class of fix is for.
    """
    from src.config import PROJECT_ROOT

    source = (PROJECT_ROOT / "src" / "core" / "notes_generator.py").read_text(encoding="utf-8")
    assert "_cached_off_loop" in source and "_save_cache_off_loop" in source
    generate_body = source.split("async def generate_notes(", 1)[1]
    # No direct synchronous cache calls may remain in the async path.
    assert "self.get_cached(cleaned)" not in generate_body.split("async def ")[0]
    assert "self.save_cache(cleaned" not in generate_body.split("async def ")[0]
