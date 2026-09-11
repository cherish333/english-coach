"""Web article importer for English Coach.
Fetches web pages, extracts article text, cleans noise, and converts content into
structured, paginated PDF documents for the textbook/sentence player.
"""

import html
from pathlib import Path
import re
from typing import Optional
from urllib.parse import urlparse

import httpx
import pymupdf


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 EnglishCoach/1.0"
)


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


async def fetch_web_page(url: str, timeout: float = 15.0) -> str:
    """Fetch raw HTML of a web page using httpx."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                raise ValueError(f"无法访问该网页，服务器返回 HTTP 状态码: {resp.status_code}")
            return resp.text
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
        r"<meta\s+property=[\"']og:title[\"']\s+content=[\"'](.*?)[\"']",
        raw_html,
        re.IGNORECASE,
    )
    if og_title:
        title = html.unescape(og_title.group(1)).strip()

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

    # 3. Locate main article container if semantic tags exist
    content = ""
    for pattern in [
        r"<article[^>]*>(.*?)</article>",
        r"<main[^>]*>(.*?)</main>",
        r"<div[^>]+class=[\"'][^\"']*(?:article|post-content|entry-content|manual__content|story-body|main-content)[^\"']*[\"'][^>]*>(.*?)</div>",
        r"<div[^>]+id=[\"'][^\"']*(?:article|post-content|entry-content|main-content)[^\"']*[\"'][^>]*>(.*?)</div>",
    ]:
        m = re.search(pattern, cleaned_html, re.IGNORECASE | re.DOTALL)
        if m:
            content = m.group(1)
            break

    if not content:
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


def build_article_pdf(title: str, paragraphs: list[str], url: str = "") -> tuple[bytes, int]:
    """Generate a clean, readable A4 PDF from article paragraphs.
    Formats pages into comfortable learning chunks (~180-260 words per page).
    Returns (pdf_bytes, page_count).
    """
    doc = pymupdf.open()
    width, height = 595, 842  # Standard A4
    margin_x = 50
    margin_top = 45
    margin_bottom = 35
    max_y = height - margin_bottom - 20

    # Group paragraphs into pages
    pages_data: list[list[str]] = []
    current_page: list[str] = []
    current_words = 0

    for p in paragraphs:
        w_count = len(p.split())
        # Break into next page if current page has sufficient content
        if current_words + w_count > 240 and len(current_page) >= 2:
            pages_data.append(current_page)
            current_page = [p]
            current_words = w_count
        else:
            current_page.append(p)
            current_words += w_count

    if current_page:
        pages_data.append(current_page)
    if not pages_data:
        pages_data = [[title]]

    total_pages = len(pages_data)

    for page_idx, p_list in enumerate(pages_data, 1):
        page = doc.new_page(width=width, height=height)

        # Header metadata placed at y=10-18 (so DocumentLibrary b[1] < 20 ignores it)
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

        # Page Title
        if page_idx == 1:
            title_rect = pymupdf.Rect(margin_x, y, width - margin_x, y + 42)
            page.insert_textbox(title_rect, title, fontname="helv", fontsize=15)
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
            page.insert_textbox(
                pymupdf.Rect(margin_x, y, width - margin_x, y + 15),
                running_title,
                fontname="helv",
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

        # Paragraphs
        for p in p_list:
            est_lines = max(1, len(p) // 75 + 1)
            p_h = max(24, est_lines * 16 + 12)
            target_rect = pymupdf.Rect(
                margin_x, y, width - margin_x, min(max_y, y + p_h + 10)
            )
            page.insert_textbox(target_rect, p, fontname="helv", fontsize=11)
            y += p_h
            if y > max_y - 25:
                break

        # Running Footer at very bottom (y > height - 20 so DocumentLibrary ignores it)
        footer_rect = pymupdf.Rect(
            margin_x, height - 18, width - margin_x, height - 5
        )
        page.insert_textbox(
            footer_rect,
            f"Page {page_idx} of {total_pages}",
            fontname="helv",
            fontsize=8,
            color=(0.5, 0.5, 0.5),
            align=pymupdf.TEXT_ALIGN_RIGHT,
        )

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

    if raw_text:
        title = custom_title or (raw_text.splitlines()[0][:60] if raw_text else "Pasted Article")
        title = normalize_typography(title)
        # Split into paragraphs
        raw_paras = [p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
        paragraphs = [normalize_typography(p) for p in raw_paras if re.search(r"[a-zA-Z]", p)]
        if not paragraphs:
            raise ValueError("粘贴的内容中未检测到有效的英文段落。")
        source_desc = "用户文本导入"
        source_url = url or ""
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
