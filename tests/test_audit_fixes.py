import pytest
from fastapi.testclient import TestClient
from src.server import app, document_library
from src.core.web_importer import fetch_web_page
from src.core.documents import DocumentLibrary


@pytest.mark.anyio
async def test_ssrf_blocking_for_localhost_and_loopback():
    """Verify fetch_web_page strictly blocks local and loopback URLs from being fetched."""
    blocked_urls = [
        "http://localhost:8765/api/system/shutdown",
        "https://127.0.0.1:8765/api/documents",
        "http://127.0.0.2:8080",
        "http://0.0.0.0:8000",
        "http://[::1]:8000",
    ]
    for u in blocked_urls:
        with pytest.raises(ValueError, match="安全限制：禁止导入本地回环地址"):
            await fetch_web_page(u)


def test_delete_document_rejects_invalid_id():
    """Verify delete_document endpoint rejects path traversal or invalid document identifiers."""
    with TestClient(app) as client:
        # Invalid / traversal characters
        resp = client.delete("/api/documents/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)

        resp2 = client.delete("/api/documents/custom;rm -rf /")
        assert resp2.status_code == 400
        assert "Invalid document identifier" in resp2.json()["detail"]


def test_add_custom_document_avoids_in_memory_spec_collision(tmp_path):
    """Verify add_custom_document does not overwrite existing in-memory spec if same stem is used."""
    import pymupdf
    # Create simple 1-page PDF
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    pdf_bytes = doc.tobytes()
    doc.close()

    lib = DocumentLibrary()
    doc1 = lib.add_custom_document("test_collision.pdf", pdf_bytes, title="Collision 1")
    id1 = doc1["id"]

    try:
        assert id1 in lib.specs
        # Add another document with identical filename
        doc2 = lib.add_custom_document("test_collision.pdf", pdf_bytes, title="Collision 2")
        id2 = doc2["id"]

        assert id2 in lib.specs
        assert id1 != id2
        assert lib.specs[id1].title == "Collision 1"
        assert lib.specs[id2].title == "Collision 2"
    finally:
        try:
            lib.delete_custom_document(id1)
        except Exception:
            pass
        try:
            lib.delete_custom_document(id2)
        except Exception:
            pass


def test_cross_page_sentence_timestamp_fallback(tmp_path):
    """Verify get_page_sentences matches sentence timestamps across page boundaries if has_page_field is True."""
    from src.core.web_importer import build_article_pdf

    title = "Cross Page Test"
    paragraphs = ["First unique sentence alpha.", "Second unique sentence beta."]
    pdf_bytes, pages = build_article_pdf(title, paragraphs)

    # Simulate raw_meta with page=2 for sentence that actually rendered on page 1
    timestamps = [
        {"text": "First unique sentence alpha.", "page": 99, "start_time": 10.5, "end_time": 15.2},
        {"text": "Second unique sentence beta.", "page": 1, "start_time": 16.0, "end_time": 20.0},
    ]

    doc_info = document_library.add_custom_document(
        filename="cross_page_test.pdf",
        content=pdf_bytes,
        title=title,
        sentence_timestamps=timestamps,
    )
    doc_id = doc_info["id"]

    try:
        sentences = document_library.get_page_sentences(doc_id, 1)
        # Even though "First unique sentence alpha." had page=99 in timestamps,
        # global fallback matches it when extracted on page 1
        s1 = [s for s in sentences if "First unique sentence alpha" in s.get("text", "")][0]
        assert s1["has_media"] is True
        assert s1["start_time"] == 10.5
        assert s1["end_time"] == 15.2
    finally:
        try:
            document_library.delete_custom_document(doc_id)
        except Exception:
            pass


@pytest.mark.anyio
async def test_ssrf_blocking_private_networks_and_redirects():
    """Verify fetch_web_page blocks private IP networks and malicious redirects."""
    import httpx
    from src.core.web_importer import fetch_web_page

    private_urls = [
        "http://10.0.0.1/admin",
        "http://172.16.0.5/api",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data",
    ]
    for u in private_urls:
        with pytest.raises(ValueError, match="安全限制：禁止导入本地回环地址或局域网私有IP地址"):
            await fetch_web_page(u)

    # Test SSRF block on redirect hop
    def mock_redirect_handler(request: httpx.Request) -> httpx.Response:
        if "redirect-loopback" in str(request.url):
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8765/shutdown"}, request=request)
        if "redirect-private" in str(request.url):
            return httpx.Response(302, headers={"Location": "http://192.168.1.1/secret"}, request=request)
        return httpx.Response(200, text="OK", request=request)

    from unittest.mock import patch
    real_async_client = httpx.AsyncClient

    def custom_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(mock_redirect_handler)
        return real_async_client(*args, **kwargs)

    with patch("src.core.web_importer.httpx.AsyncClient", side_effect=custom_client):
        with pytest.raises(ValueError, match="安全限制：禁止导入本地回环地址或局域网私有IP地址"):
            await fetch_web_page("http://safe-public-site.com/redirect-loopback")
        with pytest.raises(ValueError, match="安全限制：禁止导入本地回环地址或局域网私有IP地址"):
            await fetch_web_page("http://safe-public-site.com/redirect-private")


@pytest.mark.anyio
async def test_smart_notes_structural_validation_and_token_budget():
    """Verify smart notes generator validates all 3 sections before caching and uses 1600 tokens."""
    import uuid
    from unittest.mock import AsyncMock, patch, MagicMock
    from src.core.notes_generator import SmartNotesGenerator
    from src.core.notes_manager import get_db_connection

    gen = SmartNotesGenerator()
    unique_id = uuid.uuid4().hex[:8]
    test_sentence = f"Technology improves productivity across industries {unique_id}."

    # Incomplete response: only contains 2 of 3 required sections
    incomplete_md = f"""### 🎯 核心原句与释义 (Sentence)
- **原句**: `{test_sentence}`
- **中文释义**: 科技提高了各行业的生产力。

### 📚 语法结构精析 (Syntax Hierarchy)
- 主语: Technology
"""
    # Complete response: contains all 3 sections
    complete_md = incomplete_md + """
### 🧩 核心语块与生词 (Key Chunks & Vocab)
- **productivity**: 生产力
"""

    mock_resp_incomplete = MagicMock()
    mock_resp_incomplete.choices = [MagicMock(message=MagicMock(content=incomplete_md))]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp_incomplete)

    try:
        with patch.object(gen, "_get_nvidia_client", return_value=mock_client):
            res = await gen.generate_notes(test_sentence, force_refresh=True)
            # Incomplete output shouldn't be saved to cache
            assert gen.get_cached(test_sentence) is None
            # Verify max_tokens was 1600
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["max_tokens"] == 1600

        mock_resp_complete = MagicMock()
        mock_resp_complete.choices = [MagicMock(message=MagicMock(content=complete_md))]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp_complete)

        with patch.object(gen, "_get_nvidia_client", return_value=mock_client):
            res = await gen.generate_notes(test_sentence, force_refresh=True)
            assert res["success"] is True
            assert gen.get_cached(test_sentence) is not None
            assert "### 🧩" in gen.get_cached(test_sentence)
    finally:
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("DELETE FROM sentence_smart_notes WHERE sentence_text = ?", (test_sentence,))
        finally:
            conn.close()


@pytest.mark.anyio
async def test_clean_client_close_in_translation_and_notes():
    """Verify TranslationService and SmartNotesGenerator have functional async close() methods."""
    from src.core.translation import TranslationService
    from src.core.notes_generator import SmartNotesGenerator

    ts = TranslationService()
    # Trigger creation of http client
    _ = ts._get_http_client()
    assert ts._http_client is not None
    assert not ts._http_client.is_closed
    await ts.close()
    assert ts._http_client is None

    ng = SmartNotesGenerator()
    await ng.close()
    assert ng._nvidia_client is None


def test_gamification_action_status_and_no_double_counting():
    """Verify /api/game/action returns status dictionary and respects increment_words=False."""
    with TestClient(app) as client:
        # Get baseline status
        status_before = client.get("/api/game/status").json()
        words_before = status_before["words_typed"]

        resp = client.post("/api/game/action", json={
            "action_type": "typing_completed",
            "words_count": 25,
            "combo": 10,
            "increment_words": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        result = data["result"]
        # Verify status dictionary exists and contains HUD fields
        assert "status" in result
        assert "level" in result["status"]
        assert "title" in result["status"]
        assert "streak_days" in result["status"]

        # Verify words_typed was not double counted
        status_after = client.get("/api/game/status").json()
        assert status_after["words_typed"] == words_before


def test_portrait_and_app_js_consistency():
    """Verify portrait.html and app.js have safe markdown rendering and consistent sync events."""
    from pathlib import Path

    portrait_html = (Path("src/static/portrait.html")).read_text(encoding="utf-8")
    app_js = (Path("src/static/app.js")).read_text(encoding="utf-8")

    # 1. Portrait has renderMarkdownSafe and uses it in renderPortraitWhiteboard
    assert "function renderMarkdownSafe(" in portrait_html
    assert "contentEl.innerHTML = renderMarkdownSafe(pCurrentNotesMarkdown);" in portrait_html
    assert "script, iframe, object, embed" in portrait_html

    # 2. Portrait emits sentence_selected_from_portrait on bbox click
    assert 'type: "sentence_selected_from_portrait"' in portrait_html

    # 3. Portrait emits source: "portrait" on sentence navigation
    assert 'source: "portrait"' in portrait_html

    # 4. Portrait action payload uses action_type and increment_words: false, unpacks data.result.status
    assert 'action_type: "typing_completed"' in portrait_html
    assert 'increment_words: false' in portrait_html
    assert 'updatePortraitHud(data.result.status)' in portrait_html

    # 5. app.js updateSentencePreview avoids redundant sentence_selected broadcast
    assert 'type: "sentence_selected"' not in app_js[app_js.find("function updateSentencePreview") : app_js.find("saveLearningProgress")]


def test_websocket_practice_mode_prevents_conversation_contamination():
    """Verify WebSocket practice mode returns pronunciation eval and does not contaminate conversation history."""
    from unittest.mock import patch, MagicMock
    import numpy as np

    mock_asr = MagicMock()
    mock_asr.transcribe.return_value = "The quick brown fox jumps over the lazy dog."

    client = TestClient(app)
    with patch("src.server.get_asr", return_value=mock_asr):
        with patch("src.server.VadProcessor") as MockVadClass:
            mock_vad = MagicMock()
            MockVadClass.return_value = mock_vad
            # Return 0.5s of audio to exceed minimum 0.25s threshold
            mock_vad.get_speech_audio.return_value = np.zeros(8000, dtype=np.float32)

            with client.websocket_connect("/ws/chat") as ws:
                # 1. Trigger practice mode on a specific sentence
                ws.send_json({
                    "type": "lecture_sentence",
                    "text": "The quick brown fox jumps over the lazy dog.",
                    "action": "practice",
                    "index": 1
                })
                # Drain initial lecture setup messages until status Listening...
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "status" and "Listening" in msg.get("text", ""):
                        break

                # 2. Simulate speech transcription completion via audio_end
                ws.send_json({"type": "audio_end"})

                # 3. Read events after audio_end
                # Expected sequence:
                # - status: "Transcribing speech..."
                # - pronunciation_eval (evaluation with score)
                # - status: "跟读评测完成，请查看打分反馈"
                # MUST NOT receive any coach turn events (e.g. coach_start, coach_chunk, user_transcript for turn)
                m1 = ws.receive_json()
                assert m1["type"] == "status"
                assert "Transcribing" in m1["text"]

                m2 = ws.receive_json()
                assert m2["type"] == "pronunciation_eval"
                assert m2["eval"]["score"] == 100
                assert m2["eval"]["target_text"] == "The quick brown fox jumps over the lazy dog."

                m3 = ws.receive_json()
                assert m3["type"] == "status"
                assert m3["text"] == "跟读评测完成，请查看打分反馈"


def test_websocket_practice_mode_empty_speech_recovers():
    """Verify WebSocket practice mode recovers cleanly to Listening... when transcribed speech is empty."""
    from unittest.mock import patch, MagicMock
    import numpy as np

    mock_asr = MagicMock()
    mock_asr.transcribe.return_value = ""

    client = TestClient(app)
    with patch("src.server.get_asr", return_value=mock_asr):
        with patch("src.server.VadProcessor") as MockVadClass:
            mock_vad = MagicMock()
            MockVadClass.return_value = mock_vad
            mock_vad.get_speech_audio.return_value = np.zeros(8000, dtype=np.float32)

            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({
                    "type": "lecture_sentence",
                    "text": "The quick brown fox jumps over the lazy dog.",
                    "action": "practice",
                    "index": 1
                })
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "status" and "Listening" in msg.get("text", ""):
                        break

                ws.send_json({"type": "audio_end"})

                m1 = ws.receive_json()
                assert m1["type"] == "status" and "Transcribing" in m1["text"]

                m2 = ws.receive_json()
                assert m2["type"] == "status" and "Listening" in m2["text"]

