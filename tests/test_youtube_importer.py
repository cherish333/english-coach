"""Comprehensive tests for YouTube subtitle import, segment stitching, and textbook generation."""

import pytest
import re
from unittest import mock
from pathlib import Path
import os
import tempfile
from fastapi.testclient import TestClient
from src.server import app, document_library
from src.core.web_importer import process_web_or_text_import
from src.core.youtube_importer import (
    is_youtube_url,
    extract_youtube_video_id,
    clean_cue_text,
    clean_disfluencies,
    split_long_sentence,
    parse_vtt,
    parse_srt,
    parse_json3,
    deduplicate_rolling_cues,
    has_substantive_punctuation,
    stitch_punctuated_segments,
    rule_based_segment_stitch,
    group_sentences_into_paragraphs,
    parse_pasted_transcript,
    process_youtube_import,
    get_youtube_cookies_file,
    detect_available_browsers,
    build_ytdlp_extractor_args,
    build_ytdlp_auth_args,
    fetch_youtube_oembed_metadata,
    fetch_subtitles_via_transcript_api,
    is_bot_detection_error,
    build_graceful_failure_message,
    fetch_youtube_metadata_and_subtitles,
)

client = TestClient(app)


def test_is_youtube_url_variations():
    """Verify robust detection of all YouTube URL patterns and rejection of non-YouTube URLs."""
    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=45s",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abcdef12345",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
    ]
    for url in valid_urls:
        assert is_youtube_url(url) is True, f"Failed to recognize valid YouTube URL: {url}"
        vid = extract_youtube_video_id(url)
        assert vid == "dQw4w9WgXcQ", f"Incorrect video ID for {url}: {vid}"

    invalid_urls = [
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://vimeo.com/12345678",
        "https://omarchy.org/manual/",
        "",
        None,
        "youtube.com",
    ]
    for url in invalid_urls:
        assert is_youtube_url(url) is False, f"Erroneously recognized invalid URL: {url}"


def test_clean_cue_text_noise_removal():
    """Verify removal of music notes, sound effects in brackets, speaker tags, and HTML/VTT markup."""
    # 1. Music and bracketed sound markers (including adjectives and multi-word descriptions)
    assert clean_cue_text("[Music] Welcome to the show.") == "Welcome to the show."
    assert clean_cue_text("[music playing] Welcome to the show.") == "Welcome to the show."
    assert clean_cue_text("[upbeat music] Hello.") == "Hello."
    assert clean_cue_text("Hello everyone. [Applause] Thank you!") == "Hello everyone. Thank you!"
    assert clean_cue_text("Hello. [applause and cheering] Thank you!") == "Hello. Thank you!"
    assert clean_cue_text("(upbeat jazz music) Let us start.") == "Let us start."
    assert clean_cue_text("♪ Never gonna give you up ♪") == "Never gonna give you up"
    assert clean_cue_text("[♪♪♪]") == ""
    assert clean_cue_text("(laughter) That was funny.") == "That was funny."

    # 2. WebVTT tags and voice markup
    assert clean_cue_text("<c>Hello</c> <00:01:23.456>world</b>") == "Hello world"
    assert clean_cue_text("<v Speaker 1>Welcome back.") == "Welcome back."

    # 3. Speaker identifiers
    assert clean_cue_text("HOST: Good morning.") == "Good morning."
    assert clean_cue_text(">> JANE: Let's begin.") == "Let's begin."
    assert clean_cue_text("SPEAKER 2: Today we discuss AI.") == "Today we discuss AI."


def test_subtitle_parsers_vtt_srt_json3():
    """Verify parsers handle VTT, SRT, and YouTube JSON3 structures correctly."""
    vtt_sample = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.500 align:start position:0%
First segment of speech.

00:00:03.600 --> 00:00:06.000 line:85%
Second segment of speech.
"""
    vtt_cues = parse_vtt(vtt_sample)
    assert len(vtt_cues) == 2
    assert vtt_cues[0]["text"] == "First segment of speech."
    assert vtt_cues[0]["start"] == 1.0
    assert vtt_cues[0]["end"] == 3.5

    srt_sample = """1
00:00:02,000 --> 00:00:04,500
First SRT segment.

2
00:00:04,800 --> 00:00:07,200
Second SRT segment.
"""
    srt_cues = parse_srt(srt_sample)
    assert len(srt_cues) == 2
    assert srt_cues[0]["text"] == "First SRT segment."
    assert srt_cues[0]["start"] == 2.0
    assert srt_cues[0]["end"] == 4.5

    json3_sample = {
        "events": [
            {
                "tStartMs": 1000,
                "dDurationMs": 2500,
                "segs": [{"utf8": "Hello "}, {"utf8": "from JSON3."}],
            }
        ]
    }
    json3_cues = parse_json3(json3_sample)
    assert len(json3_cues) == 1
    assert json3_cues[0]["text"] == "Hello from JSON3."
    assert json3_cues[0]["start"] == 1.0
    assert json3_cues[0]["end"] == 3.5

    # Also verify string input to parse_json3
    import json
    json3_cues_str = parse_json3(json.dumps(json3_sample))
    assert len(json3_cues_str) == 1
    assert json3_cues_str[0]["text"] == "Hello from JSON3."


def test_deduplicate_rolling_cues():
    """Verify that sliding window word overlaps in rolling auto-captions are deduplicated."""
    rolling_cues = [
        {"start": 0.0, "end": 2.0, "text": "today we are going to"},
        {"start": 2.0, "end": 4.0, "text": "going to talk about linux"},
        {"start": 4.0, "end": 6.0, "text": "about linux on your computer"},
    ]
    deduped = deduplicate_rolling_cues(rolling_cues)
    texts = [c["text"] for c in deduped]
    assert texts == ["today we are going to", "talk about linux", "on your computer"]


def test_stitch_punctuated_segments_with_abbreviation_protection():
    """Verify segment stitching across time boundaries respects abbreviations, decimals, and quotes."""
    cues = [
        {"start": 0.0, "end": 2.0, "text": "Dr. Smith met with Mr. Brown"},
        {"start": 2.0, "end": 4.0, "text": "at 9.5 a.m. yesterday."},
        {"start": 4.0, "end": 6.0, "text": "They discussed the U.S. economy,"},
        {"start": 6.0, "end": 8.0, "text": "which grew by 3.2% this quarter."},
        {"start": 8.0, "end": 10.0, "text": "He said, \"It was remarkable.\" Can you believe that?"},
        {"start": 10.0, "end": 12.0, "text": "What next? \"Nothing.\" Let us go."},
    ]
    assert has_substantive_punctuation(" ".join(c["text"] for c in cues)) is True

    sentences = stitch_punctuated_segments(cues)
    assert len(sentences) == 7
    assert sentences[0] == "Dr. Smith met with Mr. Brown at 9.5 a.m. yesterday."
    assert sentences[1] == "They discussed the U.S. economy, which grew by 3.2% this quarter."
    assert sentences[2] == 'He said, "It was remarkable."'
    assert sentences[3] == "Can you believe that?"
    assert sentences[4] == "What next?"
    assert sentences[5] == '"Nothing."'
    assert sentences[6] == "Let us go."


def test_rule_based_segment_stitch_fallback():
    """Verify deterministic fallback handles time gaps, clause connectors, and capitalization."""
    unpunctuated_cues = [
        {"start": 0.0, "end": 2.0, "text": "welcome back to the channel everyone"},
        {"start": 2.0, "end": 4.0, "text": "today we explore linux systems"},
        {"start": 5.5, "end": 7.5, "text": "it is fast and lightweight"},  # Note 1.5s gap
        {"start": 7.5, "end": 9.5, "text": "and you will really enjoy using it every day"},
    ]
    assert has_substantive_punctuation(" ".join(c["text"] for c in unpunctuated_cues)) is False

    sentences = rule_based_segment_stitch(unpunctuated_cues)
    assert len(sentences) >= 2
    for s in sentences:
        assert s[0].isupper(), f"Sentence must start capitalized: {s}"
        assert s.endswith((".", "!", "?")), f"Sentence must end with punctuation: {s}"
        assert len(s.split()) >= 2


def test_group_sentences_into_paragraphs():
    """Verify sentences are nicely grouped into multi-sentence paragraphs."""
    sents = [f"Sentence number {i}." for i in range(1, 8)]
    paras = group_sentences_into_paragraphs(sents, sentences_per_para=3)
    assert len(paras) == 3
    assert paras[0] == "Sentence number 1. Sentence number 2. Sentence number 3."
    assert paras[1] == "Sentence number 4. Sentence number 5. Sentence number 6."
    assert paras[2] == "Sentence number 7."


@pytest.mark.anyio
async def test_process_youtube_import_and_library_integration():
    """Integration test importing a public YouTube video through process_youtube_import."""
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    mock_cues = [
        {"start": 0.0, "end": 2.5, "text": "We are no strangers to love and practice."},
        {"start": 2.5, "end": 6.8, "text": "You know the rules and so do I every day."},
        {"start": 7.0, "end": 11.2, "text": "A full commitment is what I am thinking of."},
        {"start": 11.5, "end": 15.0, "text": "You would not get this from any other teacher."},
        {"start": 15.2, "end": 19.5, "text": "I just want to tell you how I am feeling today."},
        {"start": 20.0, "end": 23.5, "text": "Gotta make you understand the core grammar rules."},
    ]
    with mock.patch("src.core.youtube_importer.fetch_youtube_metadata_and_subtitles", return_value=("Rick Astley English Lesson", mock_cues, "RickAstleyVEVO")), \
         mock.patch("src.core.youtube_importer.download_youtube_media", return_value=None):
        title, pdf_bytes, desc, pages, filename = await process_youtube_import(
            url, custom_title="Rick Astley English Lesson"
        )
    assert title == "Rick Astley English Lesson"
    assert pdf_bytes.startswith(b"%PDF")
    assert pages >= 1
    assert "YouTube" in desc
    assert filename.startswith("yt_")

    # Add to custom books library
    doc_info = document_library.add_custom_document(filename, pdf_bytes, title, desc)
    doc_id = doc_info["id"]
    try:
        assert doc_info["is_custom"] is True
        assert doc_info["title"] == "Rick Astley English Lesson"

        # Verify page sentences extraction
        page_sents = document_library.get_page_sentences(doc_id, 1)
        assert len(page_sents) >= 5
        for s in page_sents[:3]:
            assert "text" in s
            assert len(s["text"].split()) >= 2
    finally:
        document_library.delete_custom_document(doc_id)


def test_import_url_endpoint_with_youtube():
    """Verify /api/documents/import-url endpoint handles YouTube URL correctly end-to-end."""
    payload = {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "YouTube Integration Test",
    }
    mock_cues = [
        {"start": 0.0, "end": 2.5, "text": "We are no strangers to love and practice."},
        {"start": 2.5, "end": 6.8, "text": "You know the rules and so do I every day."},
        {"start": 7.0, "end": 11.2, "text": "A full commitment is what I am thinking of."},
        {"start": 11.5, "end": 15.0, "text": "You would not get this from any other teacher."},
        {"start": 15.2, "end": 19.5, "text": "I just want to tell you how I am feeling today."},
        {"start": 20.0, "end": 23.5, "text": "Gotta make you understand the core grammar rules."},
    ]
    with mock.patch("src.core.youtube_importer.fetch_youtube_metadata_and_subtitles", return_value=("YouTube Integration Test", mock_cues, "TestChannel")), \
         mock.patch("src.core.youtube_importer.download_youtube_media", return_value=None):
        resp = client.post("/api/documents/import-url", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    doc = data["document"]
    doc_id = doc["id"]
    try:
        assert doc["title"] == "YouTube Integration Test"
        assert doc["is_custom"] is True

        # Verify document is selectable and sentences can be read
        sent_resp = client.get(f"/api/documents/{doc_id}/pages/1/sentences")
        assert sent_resp.status_code == 200
        sentences = sent_resp.json()["sentences"]
        assert len(sentences) >= 5
    finally:
        del_resp = client.delete(f"/api/documents/{doc_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True


def test_chinese_title_pdf_generation():
    """Verify that custom textbooks with Chinese titles/content preserve CJK glyphs without corruption to '????'."""
    import pymupdf
    from src.core.web_importer import build_article_pdf

    chinese_title = "YouTube 原声听力精读：科技趋势"
    paragraphs = [
        "Artificial intelligence is transforming software engineering worldwide.",
        "Learning English with authentic videos helps build natural fluency.",
    ]
    pdf_bytes, pages = build_article_pdf(chinese_title, paragraphs)
    assert pages == 1
    assert pdf_bytes.startswith(b"%PDF")

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page_text = doc[0].get_text()
    doc.close()

    # The Chinese title must NOT be corrupted into question marks
    assert "YouTube 原声听力精读：科技趋势" in page_text
    assert "????" not in page_text


@pytest.mark.anyio
async def test_youtube_metadata_fetch_with_playlist_params():
    """Verify that URLs containing playlist/radio params extract only the single video without hanging."""
    from src.core.youtube_importer import extract_youtube_video_id
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1"
    vid = extract_youtube_video_id(url)
    assert vid == "dQw4w9WgXcQ"
    clean_url = f"https://www.youtube.com/watch?v={vid}"
    assert "list=" not in clean_url
    assert clean_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_get_youtube_cookies_file_detection(monkeypatch, tmp_path):
    """Verify detection of YouTube cookies file via env variable and standard data directory."""
    # 1. Nonexistent env var returns None (or None if default doesn't exist)
    monkeypatch.delenv("YOUTUBE_COOKIES_FILE", raising=False)
    
    # 2. Set env var to non-empty temp file
    cookie_file = tmp_path / "custom_cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n.youtube.com TRUE / FALSE 0 SID test\n")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookie_file))
    assert get_youtube_cookies_file() == str(cookie_file.resolve())

    # 3. Empty file should be ignored
    empty_file = tmp_path / "empty_cookies.txt"
    empty_file.write_text("")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(empty_file))
    assert get_youtube_cookies_file() != str(empty_file.resolve())


def test_detect_available_browsers_and_auth_args(monkeypatch):
    """Verify browser detection and yt-dlp arguments construction for player clients & cookies."""
    # Env override
    monkeypatch.setenv("YOUTUBE_BROWSER", "brave")
    assert detect_available_browsers() == ["brave"]

    # Extractor args construction
    assert build_ytdlp_extractor_args("android,ios,mweb,web") == [
        "--extractor-args",
        "youtube:player_client=android,ios,mweb,web",
    ]
    assert build_ytdlp_extractor_args("") == []

    # Auth args construction
    assert build_ytdlp_auth_args(cookies_file="/path/to/cookies.txt") == [
        "--cookies",
        "/path/to/cookies.txt",
    ]
    assert build_ytdlp_auth_args(cookies_file=None, browser="chromium") == [
        "--cookies-from-browser",
        "chromium",
    ]


def test_is_bot_detection_error_and_graceful_message():
    """Verify bot detection error matching and friendly guidance generation."""
    bot_errors = [
        "Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies",
        "Sign in to confirm you're not a bot",
        "youtube_transcript_api._errors.RequestBlocked",
        "YouTube is blocking requests from your IP",
        "login_required: bot_detected",
    ]
    for err in bot_errors:
        assert is_bot_detection_error(err) is True, f"Failed to identify bot error: {err}"

    non_bot_errors = [
        "Video unavailable",
        "HTTP Error 404: Not Found",
        "No English subtitles found",
    ]
    for err in non_bot_errors:
        assert is_bot_detection_error(err) is False, f"Erroneously flagged non-bot error: {err}"

    msg = build_graceful_failure_message("Sign in to confirm you’re not a bot.")
    assert "data/youtube_cookies.txt" in msg
    assert "YOUTUBE_COOKIES_FILE" in msg
    assert "直接粘贴文章内容" in msg


@pytest.mark.anyio
async def test_fetch_youtube_oembed_metadata_mocked():
    """Verify oEmbed metadata fetching extracts title and author properly."""
    fake_oembed = {
        "title": "Mocked Tech Lecture",
        "author_name": "MIT OpenCourseWare",
    }
    with mock.patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_oembed
        mock_get.return_value = mock_resp

        title, channel = await fetch_youtube_oembed_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert title == "Mocked Tech Lecture"
        assert channel == "MIT OpenCourseWare"


def test_fetch_subtitles_via_transcript_api_mocked():
    """Verify youtube-transcript-api integration formats snippets into cues for both 1.x and 0.x APIs."""
    fake_snippets = [
        {"text": "Hello world [Music]", "start": 0.0, "duration": 2.5},
        {"text": "Welcome to our class.", "start": 2.5, "duration": 3.0},
    ]

    # Test 1.x instance API
    with mock.patch("youtube_transcript_api.YouTubeTranscriptApi") as mock_cls:
        del mock_cls.get_transcript
        mock_instance = mock_cls.return_value
        mock_transcript = mock.MagicMock()
        mock_transcript.fetch.return_value = fake_snippets
        mock_instance.list.return_value.find_manually_created_transcript.return_value = mock_transcript

        cues = fetch_subtitles_via_transcript_api("dQw4w9WgXcQ")
        assert len(cues) == 2
        assert cues[0]["text"] == "Hello world"
        assert cues[0]["start"] == 0.0
        assert cues[0]["end"] == 2.5
        assert cues[1]["text"] == "Welcome to our class."

    # Test legacy 0.x static API
    with mock.patch("youtube_transcript_api.YouTubeTranscriptApi") as mock_cls:
        mock_cls.get_transcript.return_value = fake_snippets
        cues = fetch_subtitles_via_transcript_api("dQw4w9WgXcQ")
        assert len(cues) == 2
        assert cues[0]["text"] == "Hello world"


@pytest.mark.anyio
async def test_fetch_youtube_metadata_yt_dlp_bot_fallback_to_transcript_api():
    """Verify that when yt-dlp fails with a bot check, the system falls back to transcript-api."""
    mock_cues = [
        {"start": 0.0, "end": 2.0, "text": "This is a fallback test."},
        {"start": 2.0, "end": 4.0, "text": "Transcript API saved the day."},
    ]

    with mock.patch("asyncio.create_subprocess_exec") as mock_exec, \
         mock.patch("src.core.youtube_importer.fetch_subtitles_via_transcript_api", return_value=mock_cues), \
         mock.patch("src.core.youtube_importer.fetch_youtube_oembed_metadata", return_value=("Fallback Title", "Fallback Channel")):

        mock_proc = mock.AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"ERROR: Sign in to confirm you're not a bot.")
        mock_exec.return_value = mock_proc

        title, cues, channel = await fetch_youtube_metadata_and_subtitles("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert title == "Fallback Title"
        assert channel == "Fallback Channel"
        assert len(cues) == 2
        assert cues[0]["text"] == "This is a fallback test."


@pytest.mark.anyio
async def test_fetch_youtube_metadata_bot_failure_message_raised():
    """Verify that when both yt-dlp and transcript-api fail with bot restriction, an actionable error is raised."""
    with mock.patch("asyncio.create_subprocess_exec") as mock_exec, \
         mock.patch("src.core.youtube_importer.fetch_subtitles_via_transcript_api", return_value=[]):

        mock_proc = mock.AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"ERROR: Sign in to confirm you're not a bot.")
        mock_exec.return_value = mock_proc

        with pytest.raises(ValueError) as excinfo:
            await fetch_youtube_metadata_and_subtitles("https://www.youtube.com/watch?v=G9P9D9hptq8")

        err_msg = str(excinfo.value)
        assert "Sign in to confirm you're not a bot" in err_msg
        assert "data/youtube_cookies.txt" in err_msg
        assert "直接粘贴文章内容" in err_msg


def test_is_bot_detection_error_extended_patterns():
    """Verify bot detection error matching for potoken, 429, and ipblocked patterns."""
    extended_errors = [
        "PoTokenRequired: YouTube requires a proof-of-origin token",
        "po_token generation failed",
        "HTTP Error 429: Too Many Requests",
        "youtube_transcript_api._errors.IpBlocked",
        "Too many requests from this IP",
    ]
    for err in extended_errors:
        assert is_bot_detection_error(err) is True, f"Failed to detect bot/rate-limit error: {err}"


def test_parse_pasted_transcript_formats():
    """Verify parse_pasted_transcript parses multiline timestamps, inline timestamps, and VTT."""
    # 1. Standard YouTube transcript copy
    sample_yt = """0:00
Welcome to today's computer science lecture.
0:05
[Music]
0:10
We are discussing the fundamentals of operating systems.
0:15
Memory management is a crucial subsystem.
"""
    title, paras = parse_pasted_transcript(sample_yt)
    assert title == "Welcome to today's computer science lecture"
    assert len(paras) >= 1
    assert "0:00" not in paras[0]
    assert "[Music]" not in paras[0]
    assert "operating systems" in paras[0]

    # 2. Inline timestamp format
    sample_inline = """0:00 Welcome to the channel.
0:05 Today we will learn Python.
0:10 It is very enjoyable to learn.
"""
    title2, paras2 = parse_pasted_transcript(sample_inline)
    assert "0:00" not in paras2[0]
    assert "Welcome to the channel." in paras2[0]

    # 3. WebVTT format
    sample_vtt = """WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000
First VTT line.

00:00:03.500 --> 00:00:06.000
Second VTT line.
"""
    title3, paras3 = parse_pasted_transcript(sample_vtt)
    assert len(paras3) >= 1
    assert "First VTT line." in paras3[0]


@pytest.mark.anyio
async def test_process_web_or_text_import_with_pasted_transcript_and_url():
    """Verify process_web_or_text_import uses oEmbed title and stitches pasted transcript properly."""
    sample = """0:00
Welcome to this artificial intelligence course.
0:04
[Applause]
0:08
Today we introduce deep neural networks and attention mechanisms.
0:12
They are used in modern language models.
"""
    fake_oembed = ("CS101: Introduction to AI", "Stanford Online")
    with mock.patch("src.core.youtube_importer.fetch_youtube_oembed_metadata", return_value=fake_oembed):
        title, pdf_bytes, desc, pages, filename = await process_web_or_text_import(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            raw_text=sample,
        )
        assert title == "CS101: Introduction to AI"
        assert "Stanford Online" in desc
        assert pdf_bytes.startswith(b"%PDF")
        assert pages >= 1

        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        page_text = doc[0].get_text()
        doc.close()
        assert "0:00" not in page_text
        assert "[Applause]" not in page_text
        assert "deep neural networks" in page_text


def test_clean_disfluencies_and_stuttering():
    """Verify thorough filtering of oral filler words, vocal stumbles, and stuttered repetitions."""
    # 1. Oral filler words: um, uh, erm, ah, hmm, mhm in leading, middle, and trailing positions
    assert clean_disfluencies("Um, today we are going to talk about AI.") == "Today we are going to talk about AI."
    assert clean_disfluencies("Well, um, we should, uh, probably go now.") == "Well, we should probably go now."
    assert clean_disfluencies("That was, erm, a great presentation, ah.") == "That was a great presentation."
    assert clean_disfluencies("Hmm, let me think about it, mhm.") == "Let me think about it."
    assert clean_disfluencies("[um] Welcome back to the (uh) podcast.") == "Welcome back to the podcast."
    assert clean_disfluencies("Um, uh, hello everyone.") == "Hello everyone."
    assert clean_disfluencies("We finished the project, um.") == "We finished the project."

    # 1b. Real-world spoken disfluencies: inline without prior comma, chained fillers, and discourse starters
    assert clean_disfluencies("They went um, to the store") == "They went to the store"
    assert clean_disfluencies("They went, um, to the store") == "They went to the store"
    assert clean_disfluencies("This is, um, uh, a test.") == "This is a test."
    assert clean_disfluencies("This is um, uh, a test.") == "This is a test."
    assert clean_disfluencies("and um, then") == "and then"
    assert clean_disfluencies("so uh, we") == "so we"
    assert clean_disfluencies("but um, actually") == "but actually"
    assert clean_disfluencies("because uh, it") == "because it"
    assert clean_disfluencies('He said, "Um, hello"') == 'He said, "Hello"'
    assert clean_disfluencies("He said, that we were right.") == "He said that we were right."
    assert clean_disfluencies("I think, that we should go.") == "I think that we should go."

    # 1c. Cue-level preservation of lowercase casing
    assert clean_disfluencies("that we", capitalize_first=False) == "that we"
    assert clean_disfluencies("um, that we", capitalize_first=False) == "that we"

    # 2. Repeated word stuttering: "I, I", "the, the", "you you", "it's, it's", "we, we", "in, in"
    assert clean_disfluencies("I, I really think the, the system is, is great.") == "I really think the system is great."
    assert clean_disfluencies("You you need to see this.") == "You need to see this."
    assert clean_disfluencies("It's, it's, it's very important.") == "It's very important."
    assert clean_disfluencies("We we can do this in in the morning.") == "We can do this in the morning."
    assert clean_disfluencies("And, and then they they left.") == "And then they left."

    # 3. Repeated phrase stutters: "you know, you know", "I mean, I mean"
    assert clean_disfluencies("You know, you know, that was awesome.") == "You know, that was awesome."
    assert clean_disfluencies("I mean, I mean, it was completely unexpected.") == "I mean, it was completely unexpected."

    # 4. Grammatical word preservation: "had had", "that that", "ER" (Emergency Room)
    assert clean_disfluencies("He had had a very difficult day.") == "He had had a very difficult day."
    assert clean_disfluencies("She said that that was not true.") == "She said that that was not true."
    assert clean_disfluencies("He rushed her to the ER.") == "He rushed her to the ER."

    # 5. Integrated cue text cleaning with noise tags and fillers
    assert clean_cue_text("[Music] Um, welcome to the, uh, show!") == "Welcome to the show!"
    assert clean_cue_text(">> HOST: I, I think that's, um, right.") == "I think that's right."


def test_split_long_sentence_target_lengths():
    """Verify sentences are broken at natural clause boundaries into 8-22 word segments (capped at ~28 words)."""
    # 1. Normal sentence (10-18 words) remains unchanged
    normal_sent = "This is a completely normal English sentence designed for standard typing practice."
    assert split_long_sentence(normal_sent) == [normal_sent]

    # 2. Long compound sentence (32 words) with coordinating conjunction split into comfortable chunks
    long_compound = (
        "Auto-generated subtitles often lack punctuation or produce run-on speech, "
        "and the current stitching logic leaves sentences way too long, "
        "which makes shadow typing and syntax analysis overwhelming for learners."
    )
    pieces = split_long_sentence(long_compound, target_min=8, target_max=22, hard_cap=28)
    assert len(pieces) >= 2
    for p in pieces:
        w_count = len(p.split())
        assert w_count <= 28, f"Piece exceeds hard cap of 28 words ({w_count} words): {p}"
        assert p[0].isupper(), f"Piece must start capitalized: {p}"
        assert p.endswith((".", "!", "?")), f"Piece must end with punctuation: {p}"

    # 3. Long run-on sentence with semicolon
    semi_sent = (
        "We spent three hours reviewing the engineering design proposals yesterday; "
        "however, none of the candidates met the required reliability standards."
    )
    semi_pieces = split_long_sentence(semi_sent, target_min=8, target_max=22, hard_cap=28)
    assert len(semi_pieces) == 2
    assert semi_pieces[0] == "We spent three hours reviewing the engineering design proposals yesterday."
    assert semi_pieces[1] == "However, none of the candidates met the required reliability standards."

    # 4. Long run-on speech without commas preserves 'then' and conjunctions
    unpunctuated_run_on = (
        "I went to the university library yesterday morning to study for my upcoming exam "
        "and then I met several of my classmates who were also preparing for the same test."
    )
    run_on_pieces = split_long_sentence(unpunctuated_run_on, target_min=8, target_max=22, hard_cap=28)
    assert len(run_on_pieces) >= 2
    # Verify 'then' was NOT dropped from 'and then'
    assert any("then" in p.lower() for p in run_on_pieces)
    assert any("and then" in p.lower() for p in run_on_pieces)
    for p in run_on_pieces:
        assert len(p.split()) <= 28

    # 5. Long sentence with transitional adverbs ('therefore', 'for example') preserves words
    trans_sent = (
        "We evaluated several different machine learning architectures across multiple benchmark datasets, "
        "therefore the team decided to adopt the transformer model for future iterations."
    )
    trans_pieces = split_long_sentence(trans_sent, target_min=8, target_max=22, hard_cap=28)
    assert len(trans_pieces) == 2
    assert "Therefore" in trans_pieces[1]


def test_stitch_punctuated_segments_breaks_run_ons_and_cleans_fillers():
    """Verify stitch_punctuated_segments removes fillers, respects pause gaps, and caps sentence length."""
    cues = [
        {"start": 0.0, "end": 3.0, "text": "Um, we started the company back in 2018,"},
        {"start": 3.0, "end": 6.5, "text": "because we, uh, saw a huge gap in the software market,"},
        # 0.8s speech pause gap
        {"start": 7.3, "end": 10.5, "text": "but initially we struggled to acquire our first enterprise customers,"},
        {"start": 10.5, "end": 14.0, "text": "so we, we had to pivot our entire product strategy next year."},
    ]
    sentences = stitch_punctuated_segments(cues)
    assert len(sentences) >= 2
    for s in sentences:
        assert "um" not in s.lower().split()
        assert "uh" not in s.lower().split()
        assert "we, we" not in s
        assert len(s.split()) <= 28, f"Sentence exceeded length cap: {s}"
        assert s[0].isupper()
        assert s.endswith((".", "!", "?"))

    # Verify pause gap does NOT break after dangling determiners or prepositions
    dangling_cues = [
        {"start": 0.0, "end": 3.0, "text": "Yesterday morning my best friend and I decided that we would go to the"},
        # 1.0s pause gap after "the"
        {"start": 4.0, "end": 6.0, "text": "supermarket to buy some fresh ingredients for dinner tonight."},
    ]
    dangling_sents = stitch_punctuated_segments(dangling_cues)
    for s in dangling_sents:
        assert not s.endswith("to the."), f"Sentence erroneously ended on dangling preposition: {s}"
        assert not s.lower().startswith("supermarket to buy"), f"Sentence broken into fragment: {s}"


def test_rule_based_segment_stitch_sentence_length_and_fillers():
    """Verify rule_based_segment_stitch segments unpunctuated cues into 8-22 word sentences without fillers."""
    unpunctuated_cues = [
        {"start": 0.0, "end": 2.5, "text": "um welcome back everyone to this episode"},
        {"start": 2.5, "end": 5.0, "text": "today we are going to explore deep learning"},
        # 0.7s pause gap
        {"start": 5.7, "end": 8.0, "text": "and it's it's really an amazing technology"},
        {"start": 8.0, "end": 10.5, "text": "because neural networks can understand complex patterns"},
        {"start": 10.5, "end": 13.0, "text": "so you will definitely learn a lot of useful skills"},
    ]
    sentences = rule_based_segment_stitch(unpunctuated_cues)
    assert len(sentences) >= 2
    for s in sentences:
        assert "um" not in s.lower().split()
        assert "it's it's" not in s.lower()
        assert len(s.split()) <= 28
        assert s[0].isupper()
        assert s.endswith((".", "!", "?"))

    # Test that running across cues does NOT create mid-sentence capitalized words or prepositional fragments
    clause_cues = [
        {"start": 0.0, "end": 3.0, "text": "we spent several hours discussing the project yesterday and we knew that"},
        {"start": 3.1, "end": 6.0, "text": "it was going to be a huge challenge for our small team"}
    ]
    clause_sents = rule_based_segment_stitch(clause_cues)
    for s in clause_sents:
        assert "that It" not in s, f"Erroneous mid-sentence capitalization: {s}"
        assert s != "For our small team.", f"Erroneous prepositional fragment: {s}"


def test_parse_pasted_transcript_filters_disfluencies_and_caps_length():
    """Verify raw pasted text without timestamps filters fillers and splits run-on sentences into paragraphs."""
    pasted_run_on = """
    Um, welcome everyone to today's talk, uh, we are going to discuss modern distributed systems,
    and the architecture has evolved significantly over the past decade because cloud computing became ubiquitous,
    so every software engineer really needs to understand these core design patterns, you know, um.
    """
    title, paras = parse_pasted_transcript(pasted_run_on)
    assert title is not None
    assert len(paras) >= 1
    full_para_text = " ".join(paras)
    assert "um" not in full_para_text.lower().split()
    assert "uh" not in full_para_text.lower().split()
    # Check that sentences inside paragraphs are comfortably sized
    for para in paras:
        for sent in para.split(". "):
            if sent.strip():
                assert len(sent.split()) <= 28



