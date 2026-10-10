"""Regression tests for the content-import pipeline fixes."""

import asyncio
import re
from pathlib import Path

import pymupdf
import pytest

from src.core.web_importer import (
    MAX_WEB_BODY_BYTES,
    _slice_balanced_div,
    build_article_pdf,
    clean_html_article,
    is_ssrf_blocked_host,
)
from src.core.youtube_importer import _is_complete_download


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return _normalize("\n".join(page.get_text() for page in doc))
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# P1: article body used to be dropped entirely on textbox overflow
# ---------------------------------------------------------------------------

def test_long_single_paragraph_is_not_dropped():
    """A ~5000-char paragraph rendered NOTHING and the import still reported success."""
    paragraph = ("The ancient library contains priceless manuscripts. " * 100).strip()
    assert len(paragraph) > 5000

    pdf_bytes, pages = build_article_pdf("A Long Article", [paragraph], "https://example.com/x")
    body = _pdf_text(pdf_bytes)

    assert "The ancient library contains priceless manuscripts." in body, (
        "the article body vanished from the generated PDF"
    )
    assert pages >= 1
    # Most of the source text should survive, not just the first line.
    assert len(body.split()) > len(paragraph.split()) * 0.7


def test_every_paragraph_survives_a_multi_page_article():
    paragraphs = [
        f"Paragraph number {i} discusses English learning, practice habits and feedback."
        for i in range(40)
    ]
    pdf_bytes, pages = build_article_pdf("Forty Paragraphs", paragraphs, "")
    body = _pdf_text(pdf_bytes)
    missing = [p for p in paragraphs if _normalize(p) not in body]
    assert not missing, f"{len(missing)} paragraphs lost, e.g. {missing[:2]}"
    assert pages >= 1


def test_mixed_short_and_long_paragraphs_keep_their_order_independent_content():
    long_paragraph = ("Consistent daily practice builds durable memory. " * 60).strip()
    pdf_bytes, _ = build_article_pdf(
        "Mixed", ["Short intro.", long_paragraph, "A short conclusion."], ""
    )
    body = _pdf_text(pdf_bytes)
    assert "Short intro." in body
    assert "A short conclusion." in body
    assert "Consistent daily practice builds durable memory." in body


def test_oversized_paragraph_splitter_never_loses_text():
    from src.core.web_importer import _split_oversized_paragraph

    text = " ".join(f"Sentence number {i} has a few words in it." for i in range(60))
    chunks = _split_oversized_paragraph(text, max_chars=200)
    assert len(chunks) > 1
    rejoined = " ".join(chunks)
    # Whitespace-insensitive equality: nothing dropped, nothing invented.
    assert _normalize(rejoined) == _normalize(text)


def test_cjk_article_renders():
    paragraphs = ["这是一段中文段落，用于测试中文文章的导入与渲染。" * 6, "Mixed 混合 paragraph."]
    pdf_bytes, _ = build_article_pdf("中文标题", paragraphs, "")
    body = _pdf_text(pdf_bytes)
    assert "中文" in body


# ---------------------------------------------------------------------------
# P1: nested <div> container truncated the article to its first child
# ---------------------------------------------------------------------------

def test_nested_div_container_is_sliced_by_depth():
    html = (
        '<div class="post-content">'
        '<div class="intro"><p>Intro paragraph text.</p></div>'
        '<div class="body"><p>Body paragraph one.</p><p>Body paragraph two.</p></div>'
        '<div class="conclusion"><p>Conclusion paragraph.</p></div>'
        "</div>"
    )
    start = html.index(">", html.index('<div class="post-content"')) + 1
    inner = _slice_balanced_div(html, start)
    for expected in ("Intro paragraph text.", "Body paragraph one.", "Body paragraph two.", "Conclusion paragraph."):
        assert expected in inner, f"{expected!r} was cut off by the depth scan"


def test_clean_html_article_keeps_nested_content():
    html = (
        "<html><head><title>A Nested Article</title></head><body>"
        '<div class="post-content">'
        '<div class="intro"><p>First paragraph of the article.</p></div>'
        '<div class="body"><p>Second paragraph of the article.</p>'
        "<p>Third paragraph of the article.</p></div>"
        "</div></body></html>"
    )
    title, paragraphs = clean_html_article(html, "https://example.com/a")
    joined = " ".join(paragraphs)
    assert "First paragraph" in joined
    assert "Second paragraph" in joined
    assert "Third paragraph" in joined, "nested container truncated extraction again"
    assert title == "A Nested Article"


# ---------------------------------------------------------------------------
# P2: og:title truncated at an apostrophe
# ---------------------------------------------------------------------------

def test_og_title_with_apostrophe_is_not_truncated():
    html = (
        '<html><head>'
        '<meta property="og:title" content="It\'s Not About the Nail: A Beginner\'s Guide">'
        "</head><body><article><p>Body text that is long enough.</p></article></body></html>"
    )
    title, _ = clean_html_article(html, "https://example.com/x")
    assert title == "It's Not About the Nail: A Beginner's Guide"


# ---------------------------------------------------------------------------
# P2: SSRF must fail closed when resolution fails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.1.1", "::1",
    "a-host-that-cannot-resolve.invalid",
])
def test_ssrf_blocks_local_and_unresolvable(host):
    assert is_ssrf_blocked_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "1.1.1.1", "8.8.8.8"])
def test_ssrf_allows_public(host):
    assert is_ssrf_blocked_host(host) is False


def test_web_body_cap_is_bounded():
    assert 0 < MAX_WEB_BODY_BYTES <= 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# P2: a stale .part download must never be installed as the lesson video
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("yt_abc123.mp4", True),
    ("yt_abc123.m4a", True),
    ("yt_abc123.f398.mp4.part", False),
    ("yt_abc123.mp4.part", False),
    ("yt_abc123.mp4.ytdl", False),
    ("yt_abc123.f137.mp4", False),
])
def test_complete_download_classification(tmp_path, name, expected):
    path = tmp_path / name
    path.write_bytes(b"x" * 32)
    assert _is_complete_download(path) is expected


def test_zero_byte_download_is_not_complete(tmp_path):
    path = tmp_path / "yt_abc.mp4"
    path.write_bytes(b"")
    assert _is_complete_download(path) is False


# ---------------------------------------------------------------------------
# P2: subprocesses must not outlive a cancelled/timed-out request
# ---------------------------------------------------------------------------

def test_communicate_with_timeout_kills_on_timeout():
    from src.core.youtube_importer import _communicate_with_timeout

    async def run():
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with pytest.raises(TimeoutError):
            await _communicate_with_timeout(proc, 0.2, "test sleep")
        # The child must be dead, not merely abandoned.
        assert proc.returncode is not None

    asyncio.run(run())


def test_communicate_with_timeout_kills_on_cancellation():
    from src.core.youtube_importer import _communicate_with_timeout

    async def run():
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task = asyncio.create_task(_communicate_with_timeout(proc, 30, "test sleep"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.returncode is not None

    asyncio.run(run())


def test_communicate_with_timeout_returns_normal_output():
    from src.core.youtube_importer import _communicate_with_timeout

    async def run():
        proc = await asyncio.create_subprocess_exec(
            "echo", "hello",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await _communicate_with_timeout(proc, 10, "test echo")
        assert stdout.strip() == b"hello"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# P1: AI practice books must not silently drop sentences
# ---------------------------------------------------------------------------

def test_practice_pdf_keeps_every_sentence():
    from src.core.ai_practice_generator import build_ai_practice_pdf

    words = "learning english requires consistent daily practice and honest feedback".split()
    sentences = [
        {"text": f"Study unit {i}. " + " ".join(words), "translation": "", "focus": ""}
        for i in range(1, 21)
    ]
    pdf_bytes, pages, annotated = build_ai_practice_pdf(
        "Practice Book", "", sentences, sentences_per_page=20
    )
    body = _pdf_text(pdf_bytes)
    missing = [s["text"] for s in sentences if _normalize(s["text"]) not in body]
    assert not missing, f"{len(missing)} practice sentences dropped: {missing[:2]}"
    assert len(annotated) == len(sentences)
    assert pages >= 1


def test_practice_pdf_handles_more_sentences_than_one_page():
    from src.core.ai_practice_generator import build_ai_practice_pdf

    sentences = [{"text": f"Short practice sentence {i}.", "translation": "", "focus": ""} for i in range(1, 51)]
    pdf_bytes, pages, annotated = build_ai_practice_pdf("Big Book", "", sentences, sentences_per_page=20)
    body = _pdf_text(pdf_bytes)
    missing = [s["text"] for s in sentences if _normalize(s["text"]) not in body]
    assert not missing
    assert pages >= 2
    assert len(annotated) == 50
