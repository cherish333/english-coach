"""Web article importer for English Coach.
Fetches web pages, extracts article text, cleans noise, and converts content into
structured, paginated PDF documents for the textbook/sentence player.
"""

import html
import ipaddress
import logging
from pathlib import Path
import re
import socket
from typing import Optional, Union
from urllib.parse import urlparse

import httpx
import pymupdf

logger = logging.getLogger("english_coach.web_importer")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 EnglishCoach/1.0"
)

# Cap on how much of a remote page is buffered before extracting text.
MAX_WEB_BODY_BYTES = 5 * 1024 * 1024

_SHARED_CARRIER_NAT = ipaddress.ip_network("100.64.0.0/10")


def _slice_balanced_div(document: str, start: int) -> str:
    """Return the inner HTML of the <div> whose content starts at ``start``.

    Non-greedy ``(.*?)</div>`` stops at the first nested closing tag and so
    returns only the container's first child. This walks the tag stream keeping
    a depth counter instead, ignoring self-closing tags.
    """
    depth = 1
    pos = start
    for match in re.finditer(r"<(/?)div\b[^>]*?(/?)>", document[start:], re.IGNORECASE):
        is_closing = bool(match.group(1))
        is_self_closing = bool(match.group(2))
        if is_self_closing:
            continue
        if is_closing:
            depth -= 1
            if depth == 0:
                return document[start:start + match.start()]
        else:
            depth += 1
        pos = start + match.end()
    return document[start:pos] if pos > start else ""


def normalize_typography(text: str) -> str:
    """Convert curly quotes, em-dashes, and special spaces to standard ASCII for typing practice."""
    replacements = {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "–": "-",
        "—": " - ",
        "…": "...",
        "\u00a0": " ",
        "\u202f": " ",
        "\u2009": " ",
        "\u3000": " ",
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)
    return re.sub(r"[ \t]+", " ", text).strip()


def _is_prohibited_ip(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_CARRIER_NAT)
    )


def is_ssrf_blocked_host(hostname: str) -> bool:
    """Check whether a hostname or IP points to loopback, private network, link-local, or reserved address."""
    if not hostname:
        return True
    hostname = hostname.strip().lower().strip("[]")
    if hostname in ("localhost", "0.0.0.0", "::1", "::"):
        return True
    if hostname.startswith("127.") or hostname.endswith(".local") or hostname.endswith(".internal"):
        return True

    try:
        ip = ipaddress.ip_address(hostname)
        return _is_prohibited_ip(ip)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        # Resolution failure must NOT be treated as "safe". httpx resolves the
        # name again when connecting, so failing open here let a host that
        # refuses our lookup still be dialled.
        return True

    for _, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            if _is_prohibited_ip(ip):
                return True
        except ValueError:
            pass

    return False


def validate_url_ssrf(url: str):
    """Validate that URL does not point to prohibited loopback or private network addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("安全限制：仅支持 HTTP 或 HTTPS 协议。")
    hostname = (parsed.hostname or "").lower()
    if not hostname or is_ssrf_blocked_host(hostname):
        raise ValueError("安全限制：禁止导入本地回环地址或局域网私有IP地址 (Localhost/Private IP)。")


async def fetch_web_page(url: str, timeout: float = 15.0) -> str:
    """Fetch raw HTML of a web page using httpx with SSRF validation across redirects."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    validate_url_ssrf(url)

    async def check_redirect(response: httpx.Response):
        if response.is_redirect:
            location = response.headers.get("Location")
            if location:
                redirect_url = str(response.url.join(location))
                validate_url_ssrf(redirect_url)

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"response": [check_redirect]}
        ) as client:
            # Streamed so a huge or binary response cannot be buffered whole into
            # the API process (which previously could hang or OOM the worker).
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise ValueError(f"无法访问该网页，服务器返回 HTTP 状态码: {resp.status_code}")

                content_type = (resp.headers.get("content-type") or "").lower()
                if content_type and not any(
                    accepted in content_type
                    for accepted in ("text/html", "application/xhtml", "text/plain", "application/xml", "text/xml")
                ):
                    raise ValueError(f"该网址返回的不是网页内容 (Content-Type: {content_type.split(';')[0]})，无法作为文章导入。")

                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_WEB_BODY_BYTES:
                        raise ValueError(
                            f"网页内容过大（超过 {MAX_WEB_BODY_BYTES // (1024 * 1024)}MB），请改用“粘贴正文”方式导入。"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)

        return body.decode(resp.encoding or "utf-8", errors="replace")
    except ValueError:
        raise
    except httpx.RequestError as exc:
        raise ValueError(f"网络连接失败，请检查网址或网络环境: {exc}")



def clean_html_article(raw_html: str, fallback_url: str = "") -> tuple[str, list[str]]:
    """Extract clean article title and structured paragraphs from raw HTML."""
    # 1. Remove scripts, styles, navigations, footers, headers, ads, and widgets
    cleaned_html = re.sub(
        r"<(script|style|nav|header|footer|aside|form|noscript|svg|iframe|button)[^>]*>.*?</\1>",
        "",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # 2. Extract title
    title = ""
    og_title = re.search(
        r"<meta\s+property=[\"']og:title[\"']\s+content=([\"'])(.*?)\1",
        raw_html,
        re.IGNORECASE | re.DOTALL,
    )
    if og_title:
        title = html.unescape(og_title.group(2)).strip()

    if not title:
        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
        if title_m:
            title = html.unescape(title_m.group(1)).strip()

    if not title:
        h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", cleaned_html, re.IGNORECASE | re.DOTALL)
        if h1_m:
            clean_h1 = re.sub(r"<[^>]+>", "", h1_m.group(1)).strip()
            title = html.unescape(clean_h1)

    title = normalize_typography(title)
    if not title:
        parsed = urlparse(fallback_url)
        title = parsed.path.strip("/").split("/")[-1] or parsed.netloc or "Imported Web Article"
        title = title.replace("-", " ").replace("_", " ").title()

    # 3. Locate main article container if semantic tags exist.
    #    <article>/<main> are paired tags so a non-greedy match is safe, but a
    #    <div> container must be sliced by depth: `(.*?)</div>` stops at the
    #    first *nested* closing tag, which silently truncated a typical
    #    WordPress page to its first child block.
    content = ""
    for pattern in [
        r"<article[^>]*>(.*?)</article>",
        r"<main[^>]*>(.*?)</main>",
    ]:
        m = re.search(pattern, cleaned_html, re.IGNORECASE | re.DOTALL)
        if m:
            content = m.group(1)
            break

    if not content:
        for pattern in [
            r"<div[^>]+class=[\"'][^\"']*(?:article|post-content|entry-content|manual__content|story-body|main-content)[^\"']*[\"'][^>]*>",
            r"<div[^>]+id=[\"'][^\"']*(?:article|post-content|entry-content|main-content)[^\"']*[\"'][^>]*>",
        ]:
            m = re.search(pattern, cleaned_html, re.IGNORECASE | re.DOTALL)
            if m:
                content = _slice_balanced_div(cleaned_html, m.end())
                if content:
                    break

    if not content:
        content = cleaned_html

    # Guard: if the chosen container contains far less readable text than the
    # whole document, the container heuristic was wrong — prefer the full page.
    if content is not cleaned_html:
        container_words = len(re.sub(r"<[^>]+>", " ", content).split())
        page_words = len(re.sub(r"<[^>]+>", " ", cleaned_html).split())
        if page_words > 40 and container_words < page_words * 0.4:
            logger.info(
                "Article container held %s words of %s on the page; using the full document instead",
                container_words, page_words,
            )
            content = cleaned_html

    # 4. Extract paragraphs and headings
    raw_blocks = re.findall(
        r"<(h[1-6]|p|li|blockquote)[^>]*>(.*?)</\1>",
        content,
        re.IGNORECASE | re.DOTALL,
    )

    ignored_phrases = {
        "table of contents",
        "getting started →",
        "← previous",
        "next →",
        "previous",
        "next",
        "share this article",
        "all rights reserved",
        "cookie policy",
        "terms of service",
        "privacy policy",
        "sign in",
        "log in",
        "subscribe",
    }

    paragraphs = []
    norm_title_clean = re.sub(r"[^a-zA-Z0-9]", "", title.lower())

    for tag, text in raw_blocks:
        clean = re.sub(r"<[^>]+>", "", text)
        clean = html.unescape(clean).strip()
        clean = normalize_typography(clean)

        if not clean or len(clean) < 8:
            continue
        if len(clean) < 18 and tag == "li":
            continue
        # Require some Latin alphabet letters for English learning
        if not re.search(r"[a-zA-Z]", clean):
            continue
        if clean.lower() in ignored_phrases:
            continue

        # Prevent duplicate title if the first paragraph repeats it
        norm_p = re.sub(r"[^a-zA-Z0-9]", "", clean.lower())
        if not paragraphs and norm_p == norm_title_clean:
            continue

        paragraphs.append(clean)

    # Fallback if no block tags matched
    if not paragraphs:
        plain_text = re.sub(r"<[^>]+>", "\n", content)
        for line in plain_text.splitlines():
            line_s = normalize_typography(html.unescape(line))
            if len(line_s) >= 20 and re.search(r"[a-zA-Z]", line_s):
                norm_p = re.sub(r"[^a-zA-Z0-9]", "", line_s.lower())
                if not paragraphs and norm_p == norm_title_clean:
                    continue
                paragraphs.append(line_s)

    return title, paragraphs


def sanitize_cjk_pdf_text(text: str) -> str:
    """Sanitize text for PyMuPDF CJK CID fonts (e.g. china-s).
    PyMuPDF built-in CID fonts only support Basic Multilingual Plane (BMP)
    characters. Emojis and astral symbols (> U+FFFF) or surrogates cause
    CID byte shifts that turn Chinese characters into mojibake.
    """
    if not text:
        return ""
    text = re.sub(r"[\U00010000-\U0010FFFF]", "", text)
    text = re.sub(r"[\uD800-\uDFFF]", "", text)
    text = re.sub(r"[\u2600-\u27BF\uE000-\uF8FF]", "", text)
    return text.strip()


def _split_oversized_paragraph(text: str, max_chars: int = 480) -> list[str]:
    """Split a paragraph that is too tall to fit on one page.

    PyMuPDF's insert_textbox renders *nothing* when the text does not fit the
    rectangle, so an over-long paragraph used to vanish silently. Splitting at
    sentence boundaries keeps every word in the document.
    """
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    # A single sentence longer than max_chars still has to fit: hard-wrap it.
    output: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            cut = chunk.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            output.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            output.append(chunk)
    return output


def build_article_pdf(title: str, paragraphs: list[str], url: str = "") -> tuple[bytes, int]:
    """Generate a clean, readable A4 PDF from article paragraphs.
    Formats pages into comfortable learning chunks (~180-260 words per page).
    Returns (pdf_bytes, page_count).

    Rendering is driven by measured heights: every textbox draw is checked, and a
    paragraph that does not fit starts a new page instead of being dropped.
    """
    title = sanitize_cjk_pdf_text(title) or "Article"

    expanded: list[str] = []
    for raw in paragraphs:
        cleaned = sanitize_cjk_pdf_text(raw)
        if cleaned:
            expanded.extend(_split_oversized_paragraph(cleaned))
    paragraphs = expanded

    doc = pymupdf.open()
    width, height = 595, 842  # Standard A4
    margin_x = 50
    margin_top = 45
    margin_bottom = 35
    max_y = height - margin_bottom - 20

    def draw_page_header(page, page_idx: int) -> float:
        """Draw the page chrome and return the starting y for body text."""
        if url:
            domain = urlparse(url).netloc or url
            source_text = f"Source: {domain} | {url[:65]}"
        else:
            source_text = "English Coach Web Reader"
        page.insert_textbox(
            pymupdf.Rect(margin_x, 10, width - margin_x, 18),
            source_text,
            fontname="helv",
            fontsize=7,
            color=(0.55, 0.55, 0.55),
        )

        y = margin_top
        if page_idx == 1:
            title_font = "china-s" if re.search(r"[\u4e00-\u9fa5]", title) else "helv"
            title_rect = pymupdf.Rect(margin_x, y, width - margin_x, y + 42)
            page.insert_textbox(title_rect, title, fontname=title_font, fontsize=15)
            y += 40
            page.draw_line(
                pymupdf.Point(margin_x, y),
                pymupdf.Point(width - margin_x, y),
                color=(0.75, 0.75, 0.75),
                width=0.8,
            )
            y += 18
        else:
            running_title = title[:65] + ("..." if len(title) > 65 else "")
            running_font = "china-s" if re.search(r"[\u4e00-\u9fa5]", running_title) else "helv"
            page.insert_textbox(
                pymupdf.Rect(margin_x, y, width - margin_x, y + 15),
                running_title,
                fontname=running_font,
                fontsize=8,
                color=(0.5, 0.5, 0.5),
            )
            y += 14
            page.draw_line(
                pymupdf.Point(margin_x, y),
                pymupdf.Point(width - margin_x, y),
                color=(0.85, 0.85, 0.85),
                width=0.5,
            )
            y += 16
        return y

    def draw_footer(page) -> None:
        page.insert_textbox(
            pymupdf.Rect(margin_x, height - 18, width - margin_x, height - 5),
            "English Coach · https://github.com/cherish333/english-coach",
            fontname="helv",
            fontsize=7,
            color=(0.65, 0.65, 0.65),
        )

    # --- Render pass -----------------------------------------------------
    # Every insert_textbox() call is checked: a negative return means PyMuPDF
    # drew *nothing* for that box, which is how article bodies used to vanish.
    page = doc.new_page(width=width, height=height)
    page_idx = 1
    y = draw_page_header(page, page_idx)
    font_size = 11.0
    line_height = font_size * 1.45

    def new_page() -> float:
        nonlocal page, page_idx, y
        draw_footer(page)
        page_idx += 1
        page = doc.new_page(width=width, height=height)
        y = draw_page_header(page, page_idx)
        return y

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        p_font = "china-s" if re.search(r"[\u4e00-\u9fa5]", paragraph) else "helv"

        if max_y - y < line_height:
            new_page()

        while True:
            box = pymupdf.Rect(margin_x, y, width - margin_x, max_y)
            leftover = page.insert_textbox(box, paragraph, fontname=p_font, fontsize=font_size)
            if leftover >= 0:
                # leftover is the unused height in the box, so the text occupied
                # (box height - leftover); advance past it plus a paragraph gap.
                used = (max_y - y) - leftover
                y += used + 10
                break

            # Did not fit from here. If this is not the first attempt on a fresh
            # page, the paragraph is taller than a whole page even after
            # splitting: cut it at a word boundary and carry the rest over.
            if y > margin_top + line_height:
                new_page()
                continue

            cut = paragraph.rfind(" ", 0, max(20, len(paragraph) // 2))
            if cut <= 0:
                cut = max(1, len(paragraph) // 2)
            head, paragraph = paragraph[:cut].strip(), paragraph[cut:].strip()
            head_leftover = page.insert_textbox(
                pymupdf.Rect(margin_x, y, width - margin_x, max_y),
                head,
                fontname=p_font,
                fontsize=font_size,
            )
            if head_leftover < 0:
                # Even the head does not fit: start a fresh page and retry.
                new_page()
                continue
            y += ((max_y - y) - head_leftover) + 10
            if not paragraph:
                break
            new_page()

    draw_footer(page)

    total_pages = doc.page_count
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes, total_pages


async def process_web_or_text_import(
    url: Optional[str] = None,
    raw_text: Optional[str] = None,
    custom_title: Optional[str] = None,
) -> tuple[str, bytes, str, int, str]:
    """Process an article import request from either URL or direct text.
    Returns (final_title, pdf_bytes, description, total_pages, safe_filename).
    """
    url = (url or "").strip()
    raw_text = (raw_text or "").strip()
    custom_title = (custom_title or "").strip()

    if not url and not raw_text:
        raise ValueError("请提供网页网址 (URL) 或直接粘贴文章内容。")

    if url and not raw_text:
        from src.core.youtube_importer import is_youtube_url, process_youtube_import
        if is_youtube_url(url):
            return await process_youtube_import(url=url, custom_title=custom_title)

    if raw_text:
        from src.core.youtube_importer import (
            is_youtube_url,
            fetch_youtube_oembed_metadata,
            parse_pasted_transcript,
        )

        suggested_title, paragraphs = parse_pasted_transcript(raw_text)
        if not paragraphs:
            raise ValueError("粘贴的内容中未检测到有效的英文段落。")

        title = custom_title
        source_desc = "用户文本导入"
        source_url = url or ""

        # If URL is a YouTube video and user didn't specify custom title, try oEmbed title
        if not title and url and is_youtube_url(url):
            try:
                oembed_title, oembed_author = await fetch_youtube_oembed_metadata(url)
                if oembed_title and oembed_title != "YouTube Video":
                    title = oembed_title
                    source_desc = f"YouTube 视频转录文稿 · {oembed_author}"
            except Exception:
                pass

        if not title:
            title = suggested_title or "Pasted Article"

        title = normalize_typography(title)
    else:
        raw_html = await fetch_web_page(url)
        extracted_title, paragraphs = clean_html_article(raw_html, fallback_url=url)
        title = custom_title or extracted_title
        if not paragraphs:
            raise ValueError("未能从该网页提取到有效的英文段落内容。")
        source_desc = f"网页导入: {url}"
        source_url = url

    pdf_bytes, total_pages = build_article_pdf(title, paragraphs, url=source_url)

    # Generate a clean filename for storage
    clean_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fa5]", "_", title)[:45].strip("_")
    if not clean_stem:
        clean_stem = "web_article"
    safe_filename = f"web_{clean_stem}.pdf"

    return title, pdf_bytes, source_desc, total_pages, safe_filename
