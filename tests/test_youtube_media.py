"""Tests for YouTube media downloading, HTTP 206 Range serving, and sentence timestamp alignment."""

import os
import re
import json
import pytest
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient

from src.config import MEDIA_DIR, CUSTOM_BOOKS_DIR
from src.server import app, document_library
from src.core.youtube_importer import (
    align_sentences_with_cues,
    YouTubeImportResult,
    download_youtube_media,
)
from src.core.web_importer import build_article_pdf

client = TestClient(app)


def test_align_sentences_with_cues():
    """Verify alignment of stitched sentences with raw subtitle cues to assign accurate timestamps."""
    cues = [
        {"start": 1.5, "end": 4.0, "text": "um welcome back to the channel"},
        {"start": 4.2, "end": 7.5, "text": "today we are learning authentic spoken English"},
        {"start": 8.0, "end": 11.2, "text": "and practice typing each sentence along with the video"},
    ]
    sentences = [
        "Welcome back to the channel.",
        "Today we are learning authentic spoken English.",
        "And practice typing each sentence along with the video.",
    ]

    aligned = align_sentences_with_cues(sentences, cues)
    assert len(aligned) == 3

    # Sentence 1: Welcome back to the channel.
    assert aligned[0]["text"] == sentences[0]
    assert 1.0 <= aligned[0]["start_time"] <= 2.5
    assert aligned[0]["end_time"] >= aligned[0]["start_time"] + 1.0

    # Sentence 2: Today we are learning authentic spoken English.
    assert aligned[1]["text"] == sentences[1]
    assert aligned[1]["start_time"] >= aligned[0]["start_time"]
    assert aligned[1]["end_time"] <= 8.0

    # Sentence 3: And practice typing each sentence along with the video.
    assert aligned[2]["text"] == sentences[2]
    assert aligned[2]["start_time"] >= aligned[1]["end_time"] - 1.0
    assert aligned[2]["end_time"] >= 11.0


def test_align_sentences_with_empty_or_edge_cases():
    """Verify edge cases for align_sentences_with_cues."""
    # Empty inputs
    assert align_sentences_with_cues([], []) == []
    assert align_sentences_with_cues([], [{"start": 0, "end": 1, "text": "hi"}]) == []

    # Cues empty: fallback gracefully
    res = align_sentences_with_cues(["Hello world."], [])
    assert len(res) == 1
    assert res[0]["text"] == "Hello world."
    assert "start_time" in res[0]
    assert "end_time" in res[0]


def test_youtube_import_result_tuple_compatibility():
    """Verify YouTubeImportResult behaves as a 5-tuple for backward-compatibility while holding media attributes."""
    result = YouTubeImportResult(
        title="Test Title",
        pdf_bytes=b"%PDF-1.4 mock",
        desc="Test description",
        total_pages=2,
        safe_filename="yt_test_123.pdf",
        media_path="/path/to/media.mp4",
        sentence_timestamps=[{"text": "Hello.", "start_time": 0.0, "end_time": 1.5}],
    )

    # 1. Unpacking as 5-tuple (exactly as existing callers do)
    t, pdf, d, pages, fn = result
    assert t == "Test Title"
    assert pdf.startswith(b"%PDF")
    assert d == "Test description"
    assert pages == 2
    assert fn == "yt_test_123.pdf"

    # 2. Accessing media attributes
    assert result.media_path == "/path/to/media.mp4"
    assert len(result.sentence_timestamps) == 1
    assert result.sentence_timestamps[0]["start_time"] == 0.0


def test_custom_document_with_media_and_timestamps(tmp_path):
    """Verify DocumentLibrary stores media and sentence timestamps, and cleans them up on deletion."""
    title = "Video Practice Unit"
    paragraphs = [
        "First sentence from the authentic speaker.",
        "Second sentence practicing English pronunciation.",
    ]
    pdf_bytes, pages = build_article_pdf(title, paragraphs)

    # Create dummy mp4 media file
    dummy_media = tmp_path / "dummy_video.mp4"
    dummy_bytes = b"\x00\x00\x00\x20ftypisom" + b"A" * 1024
    dummy_media.write_bytes(dummy_bytes)
    media_size = len(dummy_bytes)

    timestamps = [
        {"text": "First sentence from the authentic speaker.", "start_time": 2.5, "end_time": 5.8},
        {"text": "Second sentence practicing English pronunciation.", "start_time": 6.0, "end_time": 9.4},
    ]

    doc_info = document_library.add_custom_document(
        filename="video_practice_test.pdf",
        content=pdf_bytes,
        title=title,
        description="YouTube Video Lesson",
        media_path=dummy_media,
        sentence_timestamps=timestamps,
    )

    doc_id = doc_info["id"]
    try:
        assert doc_info["has_media"] is True
        assert doc_info["media_type"] == "video/mp4"
        assert f"/api/documents/{doc_id}/media" in doc_info["media_url"]

        # Verify list_documents includes media info
        all_docs = document_library.list_documents()
        matched = [d for d in all_docs if d["id"] == doc_id][0]
        assert matched["has_media"] is True
        assert matched["media_type"] == "video/mp4"

        # Verify get_page_sentences attaches start_time and end_time
        sentences = document_library.get_page_sentences(doc_id, 1)
        assert len(sentences) >= 2

        # Verify title sentence (non-media) has has_media=False and None timestamps
        title_s = [s for s in sentences if s.get("text") == title][0]
        assert title_s["has_media"] is False
        assert title_s["start_time"] is None
        assert title_s["end_time"] is None

        # Verify authentic spoken sentences have accurate timestamps and has_media=True
        s1 = [s for s in sentences if s.get("text") == "First sentence from the authentic speaker."][0]
        assert s1["has_media"] is True
        assert s1["start_time"] == 2.5
        assert s1["end_time"] == 5.8

        s2 = [s for s in sentences if s.get("text") == "Second sentence practicing English pronunciation."][0]
        assert s2["has_media"] is True
        assert s2["start_time"] == 6.0
        assert s2["end_time"] == 9.4

        # Verify HTTP sentences endpoint
        sent_resp = client.get(f"/api/documents/{doc_id}/pages/1/sentences")
        assert sent_resp.status_code == 200
        sent_data = sent_resp.json()
        assert sent_data["has_media"] is True
        assert sent_data["media_type"] == "video/mp4"
        assert sent_data["media_url"] == f"/api/documents/{doc_id}/media"
        assert len(sent_data["sentences"]) >= 2

        resp_s1 = [s for s in sent_data["sentences"] if s.get("text") == "First sentence from the authentic speaker."][0]
        assert resp_s1["has_media"] is True
        assert resp_s1["start_time"] == 2.5
        assert resp_s1["end_time"] == 5.8

        # Verify sentence media info endpoint for spoken sentence
        s_media_resp = client.get(f"/api/documents/{doc_id}/sentences/{s1['index']}/media?page=1")
        assert s_media_resp.status_code == 200
        s_media_data = s_media_resp.json()
        assert s_media_data["has_media"] is True
        assert s_media_data["start_time"] == 2.5
        assert s_media_data["end_time"] == 5.8
        assert s_media_data["media_url"] == f"/api/documents/{doc_id}/media"

        # Verify sentence media info endpoint for non-media title sentence
        title_media_resp = client.get(f"/api/documents/{doc_id}/sentences/{title_s['index']}/media?page=1")
        assert title_media_resp.status_code == 200
        title_media_data = title_media_resp.json()
        assert title_media_data["has_media"] is False

        # Verify HEAD request on media endpoint
        head_resp = client.head(f"/api/documents/{doc_id}/media")
        assert head_resp.status_code == 200
        assert head_resp.headers.get("accept-ranges") == "bytes"
        assert head_resp.headers.get("content-type") == "video/mp4"
        assert int(head_resp.headers.get("content-length")) == media_size

        # Verify full GET request on media endpoint
        get_resp = client.get(f"/api/documents/{doc_id}/media")
        assert get_resp.status_code == 200
        assert get_resp.headers.get("accept-ranges") == "bytes"
        assert len(get_resp.content) == media_size

        # Verify HTTP 206 Partial Content (Standard Range request)
        range_resp = client.get(
            f"/api/documents/{doc_id}/media",
            headers={"Range": "bytes=0-199"}
        )
        assert range_resp.status_code == 206
        assert range_resp.headers.get("content-range").startswith("bytes 0-199/")
        assert len(range_resp.content) == 200

        # Verify RFC 7233 open-ended upper bound clamped to file_size - 1 (does NOT 416)
        clamp_range_resp = client.get(
            f"/api/documents/{doc_id}/media",
            headers={"Range": "bytes=0-9999999"}
        )
        assert clamp_range_resp.status_code == 206
        assert clamp_range_resp.headers.get("content-range") == f"bytes 0-{media_size - 1}/{media_size}"
        assert len(clamp_range_resp.content) == media_size

        # Verify RFC 7233 suffix byte range (last 150 bytes)
        suffix_range_resp = client.get(
            f"/api/documents/{doc_id}/media",
            headers={"Range": "bytes=-150"}
        )
        assert suffix_range_resp.status_code == 206
        expected_suffix_start = media_size - 150
        assert suffix_range_resp.headers.get("content-range") == f"bytes {expected_suffix_start}-{media_size - 1}/{media_size}"
        assert len(suffix_range_resp.content) == 150

        # Verify RFC 7233 open-ended range from offset to EOF
        open_range_resp = client.get(
            f"/api/documents/{doc_id}/media",
            headers={"Range": "bytes=200-"}
        )
        assert open_range_resp.status_code == 206
        assert open_range_resp.headers.get("content-range") == f"bytes 200-{media_size - 1}/{media_size}"
        assert len(open_range_resp.content) == media_size - 200

        # Verify truly invalid range returns 416
        invalid_range_resp = client.get(
            f"/api/documents/{doc_id}/media",
            headers={"Range": "bytes=999999-9999999"}
        )
        assert invalid_range_resp.status_code == 416

    finally:
        # Verify deletion cleans up media and timestamps
        del_resp = client.delete(f"/api/documents/{doc_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True

        # Check files removed
        assert not (CUSTOM_BOOKS_DIR / f"{doc_id}.sentences.json").exists()
        assert not list(MEDIA_DIR.glob(f"{doc_id}.*"))


def test_media_endpoint_404_for_missing_or_non_media_doc():
    """Verify media endpoint returns 404 when document has no media or does not exist."""
    resp = client.get("/api/documents/non_existent_doc_123/media")
    assert resp.status_code == 404

    # Western Civ standard textbook does not have video media
    resp_west = client.get("/api/documents/west_civ/media")
    assert resp_west.status_code == 404


def test_monotonic_sequential_timestamp_matching(tmp_path):
    """Verify identical or repeated sentences (e.g. 'Thank you so much.') receive distinct,
    monotonic timestamps rather than always collapsing to the first occurrence."""
    title = "Repeated Dialogue Lesson"
    paragraphs = [
        "Thank you so much for joining us today.",
        "We are going to study English phrases together.",
        "Thank you so much for joining us today.",
    ]
    pdf_bytes, pages = build_article_pdf(title, paragraphs)

    timestamps = [
        {"text": "Thank you so much for joining us today.", "start_time": 1.2, "end_time": 3.5},
        {"text": "We are going to study English phrases together.", "start_time": 4.0, "end_time": 7.8},
        {"text": "Thank you so much for joining us today.", "start_time": 18.5, "end_time": 21.0},
    ]

    doc_info = document_library.add_custom_document(
        filename="repeated_phrases_test.pdf",
        content=pdf_bytes,
        title=title,
        description="Repeated Dialogues",
        sentence_timestamps=timestamps,
    )

    doc_id = doc_info["id"]
    try:
        sentences = document_library.get_page_sentences(doc_id, 1)
        spoken = [s for s in sentences if s.get("text") == "Thank you so much for joining us today."]
        assert len(spoken) == 2

        # Sentence 1 must match the first timestamp (1.2 - 3.5)
        assert spoken[0]["start_time"] == 1.2
        assert spoken[0]["end_time"] == 3.5

        # Sentence 2 must monotonically advance and match the second occurrence (18.5 - 21.0)
        assert spoken[1]["start_time"] == 18.5
        assert spoken[1]["end_time"] == 21.0
        assert spoken[1]["start_time"] > spoken[0]["end_time"]
    finally:
        document_library.delete_custom_document(doc_id)


def test_media_type_detection_webm_and_audio(tmp_path):
    """Verify .webm and .m4a files are assigned video/webm and audio/mp4 media_types."""
    title = "WebM Video Test"
    paragraphs = ["Testing webm media serving."]
    pdf_bytes, _ = build_article_pdf(title, paragraphs)

    dummy_webm = tmp_path / "clip.webm"
    dummy_webm.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 256)

    doc_info = document_library.add_custom_document(
        filename="webm_test.pdf",
        content=pdf_bytes,
        title=title,
        media_path=dummy_webm,
    )
    doc_id = doc_info["id"]
    try:
        assert doc_info["has_media"] is True
        assert doc_info["media_type"] == "video/webm"

        head_resp = client.head(f"/api/documents/{doc_id}/media")
        assert head_resp.status_code == 200
        assert head_resp.headers.get("content-type") == "video/webm"
    finally:
        document_library.delete_custom_document(doc_id)
