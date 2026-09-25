"""YouTube Subtitle & Transcript Importer for English Coach.
Fetches YouTube English subtitles/transcripts using yt-dlp (or youtube-transcript-api),
cleans noise/sound markers, stitches time-sliced segments into grammatically complete,
punctuated sentences, and converts content into structured textbooks.
"""

import asyncio
import html
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Union
from urllib.parse import urlparse, parse_qs

import httpx
from src.config import QWEN_MODEL_NAME, PROJECT_ROOT, YOUTUBE_COOKIES_FILE, YOUTUBE_BROWSER, MEDIA_DIR
from src.core.model_runtime import RoutedClient
from src.core.web_importer import normalize_typography, build_article_pdf

logger = logging.getLogger("english_coach.youtube_importer")

DEFAULT_YTDLP_PATH = "/usr/bin/yt-dlp"

YOUTUBE_URL_REGEX = re.compile(
    r"(?:https?://)?(?:(?:[\w-]+\.)?youtube\.com/(?:watch\?(?:[^\s]*&)?v=|shorts/|embed/|live/|v/)|youtu\.be/)([\w\-]{11})",
    re.IGNORECASE,
)

PUNCTUATION_RESTORATION_PROMPT = (
    "You are an expert English editor and language learning transcriber. "
    "Restore proper capitalization, punctuation (periods, commas, question marks), "
    "and grammatical sentence boundaries to the following unpunctuated spoken English speech transcript.\n"
    "Rules:\n"
    "1. Filter out oral filler words ('um', 'uh', 'erm', 'ah', 'hmm', 'mhm') and stuttering repetitions (e.g. 'I, I', 'the, the'). Keep all meaningful content words in original order.\n"
    "2. Format sentences into comfortable lengths for language learning and typing practice (ideally 8 to 22 words, never exceeding 28 words).\n"
    "3. Add proper capitalization to the beginning of each sentence and proper nouns (e.g. 'I', names, technologies).\n"
    "4. Insert periods (.), question marks (?), exclamation marks (!), and commas (,) where natural grammatical boundaries occur. Break up run-on spoken speech into distinct sentences.\n"
    "5. Output each grammatically complete sentence on its own separate line.\n"
    "6. Never output markdown codeblocks, explanations, or notes."
)


def is_youtube_url(url: Optional[str]) -> bool:
    """Determine whether a string is a valid YouTube video URL."""
    if not url or not isinstance(url, str):
        return False
    return bool(YOUTUBE_URL_REGEX.search(url.strip()))


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract 11-character YouTube video ID from URL."""
    if not url:
        return None
    m = YOUTUBE_URL_REGEX.search(url.strip())
    return m.group(1) if m else None


def get_ytdlp_executable() -> str:
    """Find the path to the yt-dlp executable."""
    custom = os.getenv("YTDLP_PATH")
    if custom and os.path.isfile(custom) and os.access(custom, os.X_OK):
        return custom
    if os.path.isfile(DEFAULT_YTDLP_PATH) and os.access(DEFAULT_YTDLP_PATH, os.X_OK):
        return DEFAULT_YTDLP_PATH
    found = shutil.which("yt-dlp")
    if found:
        return found
    return DEFAULT_YTDLP_PATH


def get_youtube_cookies_file() -> Optional[str]:
    """Find the path to a valid YouTube cookies.txt file if configured or present."""
    # 1. Environment variable override
    env_file = os.getenv("YOUTUBE_COOKIES_FILE")
    if env_file:
        p = Path(env_file).expanduser().resolve()
        if p.is_file() and p.stat().st_size > 0:
            return str(p)

    # 2. Configured or standard location under project data/youtube_cookies.txt
    try:
        if YOUTUBE_COOKIES_FILE and YOUTUBE_COOKIES_FILE.is_file() and YOUTUBE_COOKIES_FILE.stat().st_size > 0:
            return str(YOUTUBE_COOKIES_FILE.resolve())
        project_default = PROJECT_ROOT / "data" / "youtube_cookies.txt"
        if project_default.is_file() and project_default.stat().st_size > 0:
            return str(project_default.resolve())
    except Exception:
        pass

    # 3. CWD-relative data/youtube_cookies.txt
    cwd_path = Path("data/youtube_cookies.txt").resolve()
    if cwd_path.is_file() and cwd_path.stat().st_size > 0:
        return str(cwd_path)

    return None


def detect_available_browsers() -> List[str]:
    """Detect available local browsers on the Linux system supported by yt-dlp.
    Returns ordered list of browser names (e.g. ['chromium', 'chrome', 'firefox', 'brave']).
    Avoids false positives from residual empty/extension directories.
    """
    # 1. Check explicit environment override
    env_b = (os.getenv("YOUTUBE_BROWSER") or YOUTUBE_BROWSER or "").strip().lower()
    if env_b:
        return [env_b]

    detected = []
    # Known browser candidates with binary executables and user profile directories on Linux
    candidates = [
        ("chromium", ["chromium", "chromium-browser"], ["~/.config/chromium"]),
        ("chrome", ["google-chrome", "google-chrome-stable", "chrome"], ["~/.config/google-chrome"]),
        ("brave", ["brave", "brave-browser"], ["~/.config/BraveSoftware/Brave-Browser"]),
        ("firefox", ["firefox", "firefox-esr"], ["~/.mozilla/firefox", "~/.config/mozilla/firefox"]),
        ("edge", ["microsoft-edge", "microsoft-edge-stable"], ["~/.config/microsoft-edge"]),
        ("opera", ["opera"], ["~/.config/opera"]),
        ("vivaldi", ["vivaldi"], ["~/.config/vivaldi"]),
    ]

    for bname, binaries, profile_dirs in candidates:
        has_bin = any(shutil.which(b) for b in binaries)
        has_cookies_db = False
        for p in profile_dirs:
            p_path = Path(p).expanduser()
            if not p_path.is_dir():
                continue
            if bname == "firefox":
                if any(p_path.glob("*.default*/cookies.sqlite")):
                    has_cookies_db = True
                    break
            else:
                if (
                    (p_path / "Default" / "Cookies").exists()
                    or (p_path / "Default" / "Network" / "Cookies").exists()
                    or any(p_path.glob("*/Cookies"))
                ):
                    has_cookies_db = True
                    break

        if has_bin or has_cookies_db:
            detected.append(bname)

    return detected


def build_ytdlp_extractor_args(player_clients: str = "android,ios,mweb,web") -> List[str]:
    """Construct yt-dlp extractor arguments to use alternative player clients."""
    if not player_clients:
        return []
    return ["--extractor-args", f"youtube:player_client={player_clients}"]


def build_ytdlp_auth_args(
    cookies_file: Optional[str] = None,
    browser: Optional[str] = None,
) -> List[str]:
    """Construct yt-dlp authentication arguments for cookies file or browser cookies."""
    if cookies_file:
        return ["--cookies", cookies_file]
    if browser:
        return ["--cookies-from-browser", browser]
    default_cookie = get_youtube_cookies_file()
    if default_cookie:
        return ["--cookies", default_cookie]
    return []


async def fetch_youtube_oembed_metadata(video_id_or_url: str) -> Tuple[str, str]:
    """Extract video title and author/channel name via YouTube's public oEmbed API.
    Does not require authentication or player token and is resilient against bot detection.
    """
    vid = extract_youtube_video_id(video_id_or_url)
    clean_url = f"https://www.youtube.com/watch?v={vid}" if vid else video_id_or_url.strip()
    oembed_url = f"https://www.youtube.com/oembed?url={clean_url}&format=json"

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                data = resp.json()
                title = (data.get("title") or "").strip()
                author = (data.get("author_name") or "").strip()
                if title:
                    return title, author or "YouTube"
    except Exception as exc:
        logger.debug(f"oEmbed metadata fetch failed for {video_id_or_url}: {exc}")

    fallback_title = f"YouTube Video ({vid})" if vid else "YouTube Video"
    return fallback_title, "YouTube"


def fetch_subtitles_via_transcript_api(video_id: str) -> List[Dict]:
    """Fetch English transcript cues directly via youtube-transcript-api.
    This queries YouTube's Innertube transcript endpoint directly without triggering
    the media player bot-check that yt-dlp faces.
    Supports both youtube-transcript-api 1.x and 0.x.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.warning("youtube-transcript-api is not installed.")
        return []

    # Prepare requests session with modern browser User-Agent and optional cookies
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })

    cookies_file = get_youtube_cookies_file()
    if cookies_file:
        try:
            import http.cookiejar
            jar = http.cookiejar.MozillaCookieJar(cookies_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar
        except Exception as exc:
            logger.warning(f"Could not load cookies into requests session for transcript-api: {exc}")

    priority_langs = ["en", "en-US", "en-GB", "en-CA", "en-AU", "en-IE", "en-NZ", "en-orig"]

    try:
        # Check API style: 1.x vs 0.x
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            # 0.x API
            raw_snippets = YouTubeTranscriptApi.get_transcript(
                video_id,
                languages=priority_langs,
                cookies=cookies_file if cookies_file else None,
            )
        else:
            # 1.x API
            api = YouTubeTranscriptApi(http_client=session)
            transcript_list = api.list(video_id)
            chosen_transcript = None

            # 1. Prefer manually created English transcript
            try:
                chosen_transcript = transcript_list.find_manually_created_transcript(priority_langs)
            except Exception:
                pass

            # 2. Try any priority language transcript (manual or generated)
            if not chosen_transcript:
                try:
                    chosen_transcript = transcript_list.find_transcript(priority_langs)
                except Exception:
                    pass

            # 3. Try any English transcript by language code prefix
            if not chosen_transcript:
                for t in transcript_list:
                    if getattr(t, "language_code", "").startswith("en"):
                        chosen_transcript = t
                        break

            # 4. Fallback to default 'en'
            if not chosen_transcript:
                try:
                    chosen_transcript = transcript_list.find_transcript(["en"])
                except Exception:
                    pass

            if not chosen_transcript:
                return []

            raw_snippets = chosen_transcript.fetch()

        cues = []
        for s in raw_snippets:
            text = getattr(s, "text", None) if not isinstance(s, dict) else s.get("text")
            start = getattr(s, "start", 0.0) if not isinstance(s, dict) else s.get("start", 0.0)
            duration = getattr(s, "duration", 0.0) if not isinstance(s, dict) else s.get("duration", 0.0)
            cleaned = clean_cue_text(text or "")
            if cleaned:
                cues.append({
                    "start": float(start),
                    "end": float(start) + max(float(duration), 0.5),
                    "text": cleaned,
                })
        return cues
    except Exception as exc:
        logger.info(f"youtube-transcript-api failed for {video_id}: {exc}")
        return []


def is_bot_detection_error(error_text: str) -> bool:
    """Determine whether an error message or exception indicates YouTube bot detection."""
    if not error_text:
        return False
    lowered = error_text.lower()
    return any(
        kw in lowered
        for kw in [
            "sign in to confirm you’re not a bot",
            "sign in to confirm you're not a bot",
            "confirm you're not a bot",
            "confirm you’re not a bot",
            "requestblocked",
            "bot_detected",
            "blocking requests from your ip",
            "use --cookies-from-browser or --cookies",
            "login_required",
            "bot-check",
            "potoken",
            "po_token",
            "too many requests",
            "http error 429",
            "ipblocked",
        ]
    )


def build_graceful_failure_message(last_error: str) -> str:
    """Build a user-friendly, actionable failure message when YouTube import fails."""
    if is_bot_detection_error(last_error):
        return (
            "获取 YouTube 视频信息失败：YouTube 触发了防机器人验证限制 (Sign in to confirm you're not a bot)。\n"
            "系统已自动尝试多种客户端 (Android/iOS/Web) 与备用字幕引擎，仍无法绕过限制。\n\n"
            "💡 建议解决方法：\n"
            "1. 放置 Cookie 文件：从浏览器导出 YouTube 登录 Cookies 存为项目中的 data/youtube_cookies.txt（或在 .env 中配置 YOUTUBE_COOKIES_FILE）；\n"
            "2. 本地浏览器免密认证：确保本机浏览器已登录 YouTube，或在 .env 中设置 YOUTUBE_BROWSER=<chrome|chromium|firefox|brave>；\n"
            "3. 直接粘贴文稿（最快捷）：在 YouTube 视频下方点击「...」展开「显示转录文稿 / Show transcript」，复制英文文本后在弹窗中选择「直接粘贴文章内容」，即可一键排版生成教材！"
        )

    return (
        f"获取 YouTube 视频信息失败: {last_error}\n\n"
        "💡 提示：若视频无法在线解析，您也可以在 YouTube 视频简介下方点击「显示转录文稿 / Show transcript」，"
        "复制英文文稿后在导入弹窗中选择「直接粘贴文章内容」快速排版生成教材。"
    )


def clean_disfluencies(text: str, capitalize_first: Optional[bool] = None) -> str:
    """Clean oral speech disfluencies, filler words (um, uh, erm, ah, hmm, mhm),
    and vocal stuttering repetitions (I, I / the the / you you / it's, it's).
    Preserves valid words ('had had', 'that that') and uppercase acronyms like 'ER'.
    """
    if not text:
        return ""
    t = text

    m_lead = re.match(r"^\s*[\"'\u201c\u2018\(\[]*\s*([A-Za-z])", text)
    had_leading_caps = bool(m_lead and m_lead.group(1).isupper())

    # Match fillers: case-insensitive for um, uh, erm, ah, hmm, mhm, uh-huh, mm-hmm
    # but case-sensitive for er/Er (lowercase or titlecase only, NOT uppercase ER)
    filler_token = (
        r"(?:[uU][mM]+|[uU][hH]+|[eE][rR][mM]+|[aA][hH]+|[hH][mM]+|[mM][hH][mM]|"
        r"[uU][hH][- ][hH][uU][hH]|[uU][hH][- ][hH][uU][mM]|"
        r"[mM][mM][- ][hH][mM][mM]|[mM][mM][- ][mM][mM]|"
        r"er+|Er+)"
    )

    # 1. Bracketed or parenthesized oral fillers
    filler_paren_pat = re.compile(
        rf"\[\s*{filler_token}\s*\]|\(\s*{filler_token}\s*\)"
    )
    t = filler_paren_pat.sub("", t)

    # 2. Repeated 2-word phrase stutters: "you know, you know", "I mean, I mean"
    phrase_stutter_pat = re.compile(
        r"\b([a-zA-Z]+(?:'[a-zA-Z]+)?\s+[a-zA-Z]+(?:'[a-zA-Z]+)?)\b\s*[,–—\-]?\s+\1\b",
        re.IGNORECASE,
    )
    for _ in range(3):
        new_t = phrase_stutter_pat.sub(r"\1", t)
        if new_t == t:
            break
        t = new_t

    # 3. Repeated single-word stutters:
    # 3a. Any word repeated with comma or dash: "I, I", "think, think", "the, the", "it's, it's", "so, so"
    comma_stutter_pat = re.compile(
        r"\b(?!had\b|that\b)([a-zA-Z]+(?:'[a-zA-Z]+)?)\b\s*[,–—\-]\s*\1\b",
        re.IGNORECASE,
    )
    for _ in range(4):
        new_t = comma_stutter_pat.sub(r"\1", t)
        if new_t == t:
            break
        t = new_t

    # 3b. Frequent function words repeated with space: "the the", "I I", "you you", "it's it's"
    # (Note: exclude "had" to preserve past perfect "had had", exclude "that" to preserve "that that")
    doubled_words = (
        r"i|you|he|she|it|we|they|me|him|her|us|them|"
        r"the|a|an|this|these|those|my|your|his|our|their|"
        r"in|on|at|to|for|with|of|from|by|about|into|"
        r"and|but|so|or|if|because|as|"
        r"it's|that's|there's|what's|i'm|you're|we're|they're|he's|she's|"
        r"don't|can't|won't|didn't|isn't|aren't|wasn't|weren't|"
        r"is|was|are|were|will|would|can|could|should|"
        r"just|really|very"
    )
    space_stutter_pat = re.compile(
        rf"\b({doubled_words})\b\s+\1\b",
        re.IGNORECASE,
    )
    for _ in range(4):
        new_t = space_stutter_pat.sub(r"\1", t)
        if new_t == t:
            break
        t = new_t

    filler_chain = rf"(?:{filler_token}\b[\s,–—\-]*(?:and\s+)?)*{filler_token}\b"

    # 4a. Leading fillers at start of string or after quote/bracket
    lead_pat = re.compile(rf"(^|(?<=[\"'\u201c\u2018\(\[]))\s*{filler_chain}[\s,–—\-]*")
    t = lead_pat.sub(r"\1", t)

    # 4b. Trailing fillers before punctuation or end of string
    trail_pat = re.compile(rf"[\s,–—\-]+{filler_chain}(?=[\s.!?\"'\u201d\u2019\)\]]*$)")
    t = trail_pat.sub("", t)
    t = re.sub(rf"[\s,–—\-]+{filler_chain}(?=[.!?])", "", t)

    # 4c. Inline fillers with surrounding spaces/commas/dashes
    inline_pat = re.compile(
        rf"(\s*,\s*|\s+)({filler_chain})(\s*,\s*|[\s,–—\-]+)(?=(?:[a-zA-Z0-9\"'\u201c\u2018]))"
    )

    def repl(m):
        has_comma = "," in m.group(1) or "," in m.group(3)
        following_match = re.match(r"^\s*([a-zA-Z]+)", t[m.end():])
        next_word = following_match.group(1).lower() if following_match else ""

        prefix = t[:m.start() + len(m.group(1))]
        prev_match = re.search(r"([a-zA-Z]+)\s*,?\s*$", prefix)
        prev_word = prev_match.group(1).lower() if prev_match else ""

        if has_comma:
            if next_word in ("and", "but", "so", "or", "yet", "however", "although", "though", "because"):
                return ", "
            if prev_word in ("well", "yes", "no", "sure", "oh", "okay", "ok", "right"):
                return ", "
        return " "

    for _ in range(4):
        t = inline_pat.sub(repl, t)

    # 5. Clean artifact punctuation
    t = re.sub(r",\s*,+", ", ", t)
    t = re.sub(r",\s*([.!?])", r"\1", t)
    t = re.sub(r"^[,\s–—\-]+", "", t)
    t = re.sub(r",\s*$", "", t)

    # Remove pause commas separating verbs or following contractions, determiners, conjunctions, or prepositions
    stray_comma_pat = re.compile(
        r"\b([a-zA-Z]+'(?:s|re|m|ve|ll|d)|the|a|an|this|these|those|my|your|his|her|our|their|"
        r"should|would|could|can|will|must|may|might|is|was|are|were|have|has|had|do|does|did|"
        r"that|and|but|or|so|because|if|when|while|in|on|at|to|for|with|of|from|by|about|into)\s*,\s*",
        re.IGNORECASE,
    )
    t = stray_comma_pat.sub(r"\1 ", t)

    # Hesitation pause commas before complement 'that' after reporting verbs
    pause_that_pat = re.compile(
        r"\b(said|says|say|think|thinks|thought|realize|realizes|realized|"
        r"believe|believes|believed|hope|hopes|hoped|notice|notices|noticed|remember|"
        r"remembers|remembered|found|find|feels|felt|feel)\s*,\s+that\b",
        re.IGNORECASE,
    )
    t = pause_that_pat.sub(r"\1 that", t)

    # Normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()

    # Determine whether to capitalize first character
    should_caps = capitalize_first if capitalize_first is not None else had_leading_caps
    if should_caps and t:
        t = re.sub(
            r"(^|(?<=\s)[\"'\u201c\u2018\(\[]|^[\"'\u201c\u2018\(\[])([a-z])",
            lambda m: m.group(1) + m.group(2).upper(),
            t,
        )

    return t


def _is_inside_enclosure(s: str, pos: int) -> bool:
    """Check whether a string index is inside parentheses, brackets, or quotation marks."""
    prefix = s[:pos]
    if (prefix.count("(") - prefix.count(")")) > 0:
        return True
    if (prefix.count("[") - prefix.count("]")) > 0:
        return True
    if prefix.count('"') % 2 != 0:
        return True
    if (prefix.count("“") - prefix.count("”")) > 0:
        return True
    return False


def split_long_sentence(
    sent: str,
    target_min: int = 8,
    target_max: int = 22,
    hard_cap: int = 28,
) -> List[str]:
    """Break excessively long spoken or run-on sentences into comfortably sized sentences
    for typing practice and syntax analysis (ideally 8 to 22 words, capped at hard_cap ~28 words).
    Splits at natural clause boundaries (semicolons, conjunctions, relative pronouns, pause commas).
    Preserves all words and phrases without deletion.
    """
    s = sent.strip()
    if not s:
        return []

    words = s.split()
    total_words = len(words)

    # Check for semicolons first if both sides have >= 6 words
    semi_pat = re.compile(r";\s+")
    for m in semi_pat.finditer(s):
        pos = m.start()
        if not _is_inside_enclosure(s, pos):
            w1 = len(s[:pos].split())
            w2 = len(s[m.end():].split())
            if w1 >= 6 and w2 >= 6:
                h1 = s[:pos].strip()
                h2 = s[m.end():].strip()
                if not re.search(r"[.!?]$", h1):
                    h1 += "."
                if h2 and h2[0].isalpha() and h2[0].islower():
                    h2 = h2[0].upper() + h2[1:]
                if not re.search(r"[.!?]$", h2):
                    h2 += "."
                return (
                    split_long_sentence(h1, target_min, target_max, hard_cap)
                    + split_long_sentence(h2, target_min, target_max, hard_cap)
                )

    # 1. Within ideal length
    if total_words <= target_max:
        return [s]

    split_tiers = [
        # Tier 1: Semicolon, colon, em-dash
        (re.compile(r"(?:;\s+|:\s+|\s+[—–]\s+)"), 0, "punct"),
        # Tier 2: Comma + transitional adverb or subordinating/coordinating conjunction
        (
            re.compile(
                r",\s+(and\s+then|and|but|so|yet|because|although|though|however|therefore|"
                r"furthermore|moreover|meanwhile|while|whereas|since|for\s+example|in\s+fact)\s+",
                re.IGNORECASE,
            ),
            2,
            "comma_conj",
        ),
        # Tier 3: Comma + relative pronouns
        (
            re.compile(
                r",\s+(which|who|where|when|if)\s+",
                re.IGNORECASE,
            ),
            5,
            "comma_rel",
        ),
        # Tier 4: Other commas outside enclosures
        (re.compile(r",\s+"), 9, "comma"),
        # Tier 5: Conjunctions without comma followed by pronoun or determiner
        (
            re.compile(
                r"\s+(and\s+then|and|but|so|because|when|while|although|if|then)\s+"
                r"(?=(?:i|we|you|he|she|it|they|there|this|that|the|a|an)\b)",
                re.IGNORECASE,
            ),
            14,
            "bare_conj",
        ),
    ]

    def _build_halves(cand: Tuple) -> Tuple[str, str]:
        m, kind = cand
        h1 = s[:m.start()].strip()
        h1 = re.sub(r"[,;:\-—–\s]+$", "", h1).strip()
        if not re.search(r"[.!?]$", h1):
            h1 += "."

        rest = s[m.end():].strip()
        rest = re.sub(r"^[,\s;:\-—–]+", "", rest).strip()

        if kind in ("comma_conj", "bare_conj"):
            conj = m.group(1).strip()
            conj_cap = conj[0].upper() + conj[1:]
            if conj.lower() in ("however", "therefore", "furthermore", "moreover", "meanwhile", "for example", "in fact"):
                if not rest.startswith(","):
                    conj_cap += ","
            h2 = f"{conj_cap} {rest}".strip()
        elif kind == "comma_rel":
            rel = m.group(1).strip()
            rel_cap = rel[0].upper() + rel[1:]
            h2 = f"{rel_cap} {rest}".strip()
        else:  # punct, comma
            h2 = rest
            if h2 and h2[0].isalpha() and h2[0].islower():
                h2 = h2[0].upper() + h2[1:]

        if h2 and not re.search(r"[.!?]$", h2):
            h2 += "."
        return h1, h2

    # 2. Sentences between target_max (22) and hard_cap (28):
    # Only split if there is a clean high-priority boundary (Tier 1, 2, 3) where both sides >= target_min
    if total_words <= hard_cap:
        clean_split_found = False
        best_candidate = None
        best_score = 999

        for pat, tier_weight, kind in split_tiers[:3]:
            for m in pat.finditer(s):
                pos = m.start()
                if _is_inside_enclosure(s, pos):
                    continue
                w1 = len(s[:pos].split())
                w2 = len(s[m.end():].split())
                if w1 >= target_min and w2 >= target_min:
                    score = abs(w1 - w2) + tier_weight
                    if score < best_score:
                        best_score = score
                        best_candidate = (m, kind)
                        clean_split_found = True

        if not clean_split_found:
            return [s]

        h1, h2 = _build_halves(best_candidate)
        return [h1, h2]

    # 3. Sentences exceeding hard_cap (> 28 words): MUST split!
    best_candidate = None
    best_score = 9999

    for min_w in (target_min, 6, 4):
        for pat, tier_weight, kind in split_tiers:
            for m in pat.finditer(s):
                pos = m.start()
                if _is_inside_enclosure(s, pos):
                    continue
                w1 = len(s[:pos].split())
                w2 = len(s[m.end():].split())
                if w1 >= min_w and w2 >= min_w:
                    score = abs(w1 - w2) + tier_weight
                    if score < best_score:
                        best_score = score
                        best_candidate = (m, kind)
        if best_candidate is not None:
            break

    # Fallback: if no regex matched, split near midpoint on space
    if best_candidate is None:
        target_mid = total_words // 2
        spaces = [m.start() for m in re.finditer(r"\s+", s) if not _is_inside_enclosure(s, m.start())]
        if spaces:
            best_space = min(spaces, key=lambda sp: abs(len(s[:sp].split()) - target_mid))
            h1 = s[:best_space].strip()
            if not re.search(r"[.!?]$", h1):
                h1 += "."
            h2 = s[best_space + 1:].strip()
            if h2 and h2[0].isalpha() and h2[0].islower():
                h2 = h2[0].upper() + h2[1:]
            if h2 and not re.search(r"[.!?]$", h2):
                h2 += "."
            return (
                split_long_sentence(h1, target_min, target_max, hard_cap)
                + split_long_sentence(h2, target_min, target_max, hard_cap)
            )
        else:
            return [s]

    h1, h2 = _build_halves(best_candidate)
    return (
        split_long_sentence(h1, target_min, target_max, hard_cap)
        + split_long_sentence(h2, target_min, target_max, hard_cap)
    )


def clean_cue_text(text: str) -> str:
    """Clean HTML/VTT tags, speaker identifiers, sound effect markers, musical tags,
    and oral filler words / speech disfluencies."""
    if not text:
        return ""
    t = html.unescape(text)

    # 1. Remove WebVTT formatting & voice tags (<c>, </c>, <v Speaker>, <00:01:23.456>, <b>, <i>, etc.)
    t = re.sub(r"<[^>]+>", "", t)

    # 2. Remove audio/sound effect markers in brackets or parentheses
    sound_desc_pat = (
        r"\[[^\]]*(?:music|applause|laughter|cheer|chuckle|cough|sigh|throat|giggle|gasp|inaudible|"
        r"whisper|silence|snicker|groan|scream|buzz|ring|tune|song|sound|beat|shout|crying|sobb)[^\]]*\]|"
        r"\([^)]*(?:music|applause|laughter|cheer|chuckle|cough|sigh|throat|giggle|gasp|inaudible|"
        r"whisper|silence|snicker|groan|scream|buzz|ring|tune|song|sound|beat|shout|crying|sobb)[^)]*\)"
    )
    t = re.sub(sound_desc_pat, "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[♪+[^\]]*♪*\]", "", t)
    t = re.sub(r"[♪♫♩♬]+", "", t)

    # 3. Remove speaker labels (e.g. ">> ", ">> JANE: ", "SPEAKER 1: ", "NARRATOR: ")
    t = re.sub(r"^(?:>>\s*)+", "", t)
    speaker_tag_pat = (
        r"^(?:\[[A-Za-z0-9_\s]{1,16}\]|(?:\b(?:SPEAKER(?:\s*\d+)?|NARRATOR|HOST|INTERVIEWER|MAN|WOMAN|BOY|GIRL|GUEST)\b|"
        r"[A-Z0-9_]{2,15}(?:\s+[A-Z0-9_]{2,15})?)):\s*"
    )
    t = re.sub(speaker_tag_pat, "", t)

    # 4. Normalize quotes and spaces
    t = normalize_typography(t)

    # 5. Clean oral speech disfluencies & filler words
    t = clean_disfluencies(t)

    return t.strip()


def parse_vtt(vtt_text: str) -> List[Dict]:
    """Parse WebVTT content into timestamped text cues."""
    cues = []
    lines = vtt_text.splitlines()
    i = 0
    time_pattern = re.compile(
        r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})"
    )

    def parse_time(hours, mins, secs, ms) -> float:
        h = int(hours) if hours else 0
        m = int(mins)
        s = int(secs)
        millis = int(ms)
        return h * 3600 + m * 60 + s + millis / 1000.0

    while i < len(lines):
        line = lines[i].strip()
        m = time_pattern.match(line)
        if m:
            start_sec = parse_time(m.group(1), m.group(2), m.group(3), m.group(4))
            end_sec = parse_time(m.group(5), m.group(6), m.group(7), m.group(8))
            cue_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() and not time_pattern.match(lines[i].strip()):
                cue_lines.append(lines[i].strip())
                i += 1
            raw_text = " ".join(cue_lines)
            cleaned = clean_cue_text(raw_text)
            if cleaned:
                cues.append({"start": start_sec, "end": end_sec, "text": cleaned})
        else:
            i += 1

    return cues


def parse_srt(srt_text: str) -> List[Dict]:
    """Parse SubRip (SRT) format into timestamped text cues."""
    cues = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    time_pattern = re.compile(
        r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})"
    )

    def parse_time(hours, mins, secs, ms) -> float:
        h = int(hours) if hours else 0
        m = int(mins)
        s = int(secs)
        millis = int(ms)
        return h * 3600 + m * 60 + s + millis / 1000.0

    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        time_line_idx = -1
        for idx, line in enumerate(lines[:3]):
            if "-->" in line:
                time_line_idx = idx
                break
        if time_line_idx == -1:
            continue
        m = time_pattern.match(lines[time_line_idx])
        if not m:
            continue
        start_sec = parse_time(m.group(1), m.group(2), m.group(3), m.group(4))
        end_sec = parse_time(m.group(5), m.group(6), m.group(7), m.group(8))
        raw_text = " ".join(lines[time_line_idx + 1:])
        cleaned = clean_cue_text(raw_text)
        if cleaned:
            cues.append({"start": start_sec, "end": end_sec, "text": cleaned})

    return cues


def parse_json3(json_data: Union[dict, str]) -> List[Dict]:
    """Parse YouTube JSON3 timedtext structure."""
    if isinstance(json_data, str):
        try:
            json_data = json.loads(json_data)
        except Exception:
            return []
    if not isinstance(json_data, dict):
        return []
    cues = []
    events = json_data.get("events", [])
    for ev in events:
        start_ms = ev.get("tStartMs", 0)
        dur_ms = ev.get("dDurationMs", 0)
        segs = ev.get("segs", [])
        seg_text = "".join(s.get("utf8", "") for s in segs)
        cleaned = clean_cue_text(seg_text)
        if cleaned:
            cues.append({
                "start": start_ms / 1000.0,
                "end": (start_ms + dur_ms) / 1000.0,
                "text": cleaned
            })
    return cues


def deduplicate_rolling_cues(cues: List[Dict]) -> List[Dict]:
    """Deduplicate overlapping words/lines from rolling auto-caption cues."""
    if not cues:
        return []

    deduped = []
    last_text = ""

    for cue in cues:
        curr_text = cue["text"].strip()
        if not curr_text:
            continue

        if curr_text == last_text:
            continue

        # Check if current text starts with the tail of the previous text (sliding window overlap)
        last_words = last_text.split()
        curr_words = curr_text.split()

        overlap_found = False
        max_overlap = min(len(last_words), len(curr_words))
        for k in range(max_overlap, 1, -1):
            if [w.lower() for w in last_words[-k:]] == [w.lower() for w in curr_words[:k]]:
                new_words = curr_words[k:]
                if new_words:
                    deduped.append({
                        "start": cue["start"],
                        "end": cue["end"],
                        "text": " ".join(new_words)
                    })
                    last_text = curr_text
                overlap_found = True
                break

        if not overlap_found:
            deduped.append(cue)
            last_text = curr_text

    return deduped


def has_substantive_punctuation(text: str) -> bool:
    """Determine whether the raw subtitle text already contains standard grammatical punctuation."""
    words = text.split()
    if len(words) < 15:
        return bool(re.search(r"[.!?]", text))
    sentence_terminators = len(re.findall(r"[.!?](?:\s|$)", text))
    # If at least 1 sentence boundary per 30 words, consider punctuated
    return (sentence_terminators / len(words)) >= 0.03


DANGLING_WORDS = {
    # Determiners / Articles
    "the", "a", "an", "this", "that", "these", "those", "my", "your", "his", "her", "its", "our", "their",
    "each", "every", "some", "any", "no",
    # Prepositions
    "to", "of", "in", "on", "at", "for", "with", "by", "from", "about", "into", "through", "over",
    "under", "between", "without", "within", "during", "towards", "toward", "onto", "upon", "against", "across",
    # Conjunctions / Connectors
    "and", "but", "or", "nor", "so", "yet", "because", "although", "though", "if", "whether", "while", "when", "as", "than", "unless",
    # Auxiliary / Modal verbs
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    # Relative / Interrogative pronouns
    "which", "who", "whom", "whose", "what", "whatever", "whoever", "whichever",
    # Modifiers
    "very", "really", "more", "most", "too", "quite", "such",
}


def can_break_after(tok: str) -> bool:
    """Determine whether a token can legally end a sentence without leaving a dangling preposition/determiner."""
    if not tok:
        return False
    words = tok.split()
    if not words:
        return False
    clean = re.sub(r"^[^\w]+|[^\w]+$", "", words[-1]).lower()
    return bool(clean and clean not in DANGLING_WORDS)


def stitch_punctuated_segments(cues: List[Dict]) -> List[str]:
    """Stitch time-sliced segments that already possess punctuation into full sentences.
    Respects pause gaps (>=0.6s) and clause boundaries to keep sentences at optimal length (8-22 words).
    Guarantees sentences do not end on dangling prepositions, determiners, or conjunctions.
    """
    if not cues:
        return []

    # Protect abbreviations and decimals using single-pass regex replacement
    abbr_pattern = re.compile(
        r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Gov|Gen|Col|Sen|Rep|Inc|Ltd|Corp|Co|Dept|Univ|Assoc|"
        r"e\.g|i\.e|vs|vol|approx|fig|no|sec|cf|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|"
        r"a\.m|p\.m|U\.S|U\.K|E\.U)\.|\b\d+\.\d+\b",
        re.IGNORECASE,
    )
    placeholders = {}

    def _stash_abbr(m):
        ph = f"__ABBR_PH_{len(placeholders)}__"
        placeholders[ph] = m.group(0)
        return ph

    # Build stitched text, inserting sentence breaks at natural pause gaps (>= 0.6s)
    # when the preceding text has accumulated sufficient words (>= 8 words) and is not dangling
    tokens = []
    last_end = 0.0
    accumulated_words_in_current_sent = 0

    for i, cue in enumerate(cues):
        t_clean = cue.get("text", "").strip()
        if not t_clean:
            continue

        start_time = cue.get("start", 0.0)
        gap = start_time - last_end if last_end > 0 else 0.0

        # Check pause gap: if gap >= 0.6s, sufficient words, and token doesn't dangle
        if gap >= 0.6 and accumulated_words_in_current_sent >= 8:
            if tokens and can_break_after(tokens[-1]):
                prev_tok = tokens[-1]
                if prev_tok.endswith((",", ";", ":", "-", "—", "–")):
                    tokens[-1] = prev_tok[:-1] + "."
                    accumulated_words_in_current_sent = 0
                elif prev_tok.endswith((".", "!", "?")):
                    accumulated_words_in_current_sent = 0
                elif gap >= 0.9 and accumulated_words_in_current_sent >= 12:
                    tokens[-1] = prev_tok + "."
                    accumulated_words_in_current_sent = 0

        cue_words = t_clean.split()
        tokens.append(t_clean)
        accumulated_words_in_current_sent += len(cue_words)
        if t_clean.endswith((".", "!", "?")):
            accumulated_words_in_current_sent = 0
        last_end = cue.get("end", start_time)

    full_text = " ".join(tokens)
    full_text = re.sub(r"\s+", " ", full_text).strip()

    protected = abbr_pattern.sub(_stash_abbr, full_text)

    # Split by sentence-ending punctuation (including quotes and parentheses)
    split_pattern = r"(?:(?<=[.!?][\"'\u201d\u2019\)])|(?<=[.!?]))\s+"
    raw_sentences = re.split(split_pattern, protected)

    sentences = []
    for s in raw_sentences:
        # Restore placeholders
        for ph, orig in placeholders.items():
            s = s.replace(ph, orig)
        s = s.strip()
        if not s or len(s) < 3:
            continue

        # Clean speech disfluencies and filler words
        s = clean_disfluencies(s)
        if not s or len(s) < 3:
            continue

        # Capitalize first letter
        if s[0].isalpha() and s[0].islower():
            s = s[0].upper() + s[1:]
        # Ensure ending punctuation
        if not s.endswith((".", "!", "?", '"', "'", "”", "’", ")")):
            s += "."

        # Split long run-on sentences into optimal 8-22 word units (capped at ~28 words)
        sub_sents = split_long_sentence(s, target_min=8, target_max=22, hard_cap=28)
        for sub in sub_sents:
            sub = sub.strip()
            if sub and len(sub.split()) >= 1 and re.search(r"[a-zA-Z]", sub):
                sentences.append(sub)

    return sentences


async def stitch_unpunctuated_segments_with_llm(cues: List[Dict]) -> List[str]:
    """Use local LLM to restore punctuation and capitalization to unpunctuated spoken speech."""
    if not cues:
        return []

    # Group cues into natural chunks at cue boundaries / pause gaps (~160-240 words)
    chunks = []
    curr_cues = []
    curr_word_count = 0

    for i, cue in enumerate(cues):
        txt = cue.get("text", "").strip()
        if not txt:
            continue
        w_len = len(txt.split())
        curr_cues.append(cue)
        curr_word_count += w_len

        # Check pause gap to next cue (gap >= 0.6s indicates pause)
        pause = False
        if i + 1 < len(cues):
            gap = cues[i + 1]["start"] - cue["end"]
            if gap >= 0.6:
                pause = True

        if curr_word_count >= 160 and (pause or curr_word_count >= 240):
            chunks.append(" ".join(c["text"] for c in curr_cues))
            curr_cues = []
            curr_word_count = 0

    if curr_cues:
        chunks.append(" ".join(c["text"] for c in curr_cues))

    if not chunks:
        return rule_based_segment_stitch(cues)

    client = RoutedClient()
    all_sentences = []

    for chunk in chunks:
        try:
            resp = await client.chat.completions.create(
                model=QWEN_MODEL_NAME,
                messages=[
                    {"role": "system", "content": PUNCTUATION_RESTORATION_PROMPT},
                    {"role": "user", "content": chunk}
                ],
                temperature=0.1,
                max_tokens=700,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            )
            raw = (resp.choices[0].message.content or "").strip()
            # Strip markdown
            raw = re.sub(r"^```(?:[a-zA-Z0-9_\-]+)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            for line in lines:
                # If LLM returned bullet/numbered list, strip markers
                clean_line = re.sub(r"^(?:\d+[\.\)]|[\-\*•])\s*", "", line).strip()
                if clean_line:
                    clean_line = re.sub(r'^["\'“‘]|["\'”’]$', '', clean_line).strip()
                    clean_line = clean_disfluencies(clean_line)
                    if clean_line:
                        split_sents = re.split(
                            r"(?:(?<=[.!?][\"'\u201d\u2019\)])|(?<=[.!?]))\s+",
                            clean_line
                        )
                        for s in split_sents:
                            s = s.strip()
                            if s:
                                s = clean_disfluencies(s)
                                s = s[0].upper() + s[1:]
                                if not s.endswith((".", "!", "?", '"', "'", "”", "’", ")")):
                                    s += "."
                                for sub in split_long_sentence(s, target_min=8, target_max=22, hard_cap=28):
                                    sub = sub.strip()
                                    if sub and len(sub.split()) >= 2:
                                        all_sentences.append(sub)
        except Exception as exc:
            logger.warning(f"LLM punctuation restoration chunk failed: {exc}, falling back to rule-based chunking")
            return rule_based_segment_stitch(cues)

    if not all_sentences:
        return rule_based_segment_stitch(cues)

    return all_sentences


def rule_based_segment_stitch(cues: List[Dict]) -> List[str]:
    """Deterministic, rule-based fallback to stitch unpunctuated subtitle segments.
    Uses time gaps (>= 0.6s), natural clause connectors, and target sentence length (8-22 words).
    Avoids mid-sentence capitalization and dangling sentence fragments.
    """
    if not cues:
        return []

    sentences = []
    curr_words = []
    last_end = 0.0

    strong_transitions = {
        "however", "therefore", "furthermore", "moreover", "meanwhile", "otherwise", "finally"
    }
    coordinators = {
        "and", "but", "so", "because", "although", "though", "when", "while", "if"
    }
    pronouns_determiners = {
        "i", "we", "you", "he", "she", "it", "they", "there", "this", "that", "the", "a", "an"
    }

    # Flatten all words from cues with timestamps
    timed_words = []
    for cue in cues:
        raw = cue.get("text", "").strip()
        if not raw:
            continue
        cleaned_cue = clean_disfluencies(raw, capitalize_first=False)
        if not cleaned_cue:
            continue
        c_words = cleaned_cue.split()
        start = cue.get("start", 0.0)
        end = cue.get("end", start)
        dur = max(end - start, 0.2)
        w_dur = dur / len(c_words)
        for i, w in enumerate(c_words):
            timed_words.append({
                "word": w,
                "start": start + i * w_dur,
                "end": start + (i + 1) * w_dur,
                "is_cue_start": (i == 0),
                "cue_gap": (start - last_end) if (i == 0 and last_end > 0) else 0.0,
            })
        last_end = end

    for idx, tw in enumerate(timed_words):
        w = tw["word"].strip()
        if not w:
            continue
        w_clean = re.sub(r"^[^\w]+|[^\w]+$", "", w).lower()
        n_words = len(curr_words)

        # Check pause gap if at cue start
        is_pause = (tw["cue_gap"] >= 0.6 and n_words >= 8) or (tw["cue_gap"] >= 1.0 and n_words >= 5)
        last_word_dangles = (len(curr_words) > 0 and not can_break_after(curr_words[-1]))

        # Lookahead to next word to check if coordinator connects an independent clause
        next_word = ""
        if idx + 1 < len(timed_words):
            next_word = re.sub(r"^[^\w]+|[^\w]+$", "", timed_words[idx + 1]["word"]).lower()

        is_independent_conj = (
            (w_clean in coordinators and (next_word in pronouns_determiners or next_word == "then"))
            or (w_clean == "and" and next_word == "then")
            or (w_clean in strong_transitions)
        )

        should_break = False
        if n_words > 0 and not last_word_dangles:
            if is_pause:
                should_break = True
            elif n_words >= 12 and is_independent_conj:
                should_break = True
            elif n_words >= 22 and (w_clean in coordinators or w_clean in strong_transitions):
                should_break = True
            elif n_words >= 26:
                should_break = True

        if should_break:
            sent_str = " ".join(curr_words).strip()
            sent_str = sent_str[0].upper() + sent_str[1:]
            if not sent_str.endswith((".", "!", "?")):
                sent_str += "."
            sentences.append(sent_str)
            curr_words = [w]
        else:
            curr_words.append(w)

    if curr_words:
        sent_str = " ".join(curr_words).strip()
        sent_str = sent_str[0].upper() + sent_str[1:]
        if not sent_str.endswith((".", "!", "?")):
            sent_str += "."
        sentences.append(sent_str)

    # Post-clean pronouns and split any sentences exceeding hard_cap
    final_sentences = []
    for s in sentences:
        s = clean_disfluencies(s)
        s_fix = re.sub(r"\b(i)\b", "I", s)
        s_fix = re.sub(r"\b(i'm)\b", "I'm", s_fix, flags=re.IGNORECASE)
        s_fix = re.sub(r"\b(i've)\b", "I've", s_fix, flags=re.IGNORECASE)
        s_fix = re.sub(r"\b(i'll)\b", "I'll", s_fix, flags=re.IGNORECASE)
        s_fix = re.sub(r"\b(i'd)\b", "I'd", s_fix, flags=re.IGNORECASE)

        if len(s_fix.split()) < 2:
            continue

        # Run length segmentation
        sub_sents = split_long_sentence(s_fix, target_min=8, target_max=22, hard_cap=28)
        for sub in sub_sents:
            sub = sub.strip()
            if sub and len(sub.split()) >= 2:
                final_sentences.append(sub)

    return final_sentences


async def fetch_youtube_metadata_and_subtitles(url: str) -> Tuple[str, List[Dict], str]:
    """Extract video title, subtitle cues, and uploader channel using yt-dlp with
    automatic player client bypass, browser/file cookies, and youtube-transcript-api fallback.
    """
    ytdlp = get_ytdlp_executable()
    vid = extract_youtube_video_id(url)
    clean_url = f"https://www.youtube.com/watch?v={vid}" if vid else url.strip()

    cookies_file = get_youtube_cookies_file()
    browsers = detect_available_browsers() if not cookies_file else []

    # Configure multiple retry strategies for yt-dlp
    strategies = []

    # If cookies file is present, standard client gives full access to all manual and automatic caption tracks
    if cookies_file:
        strategies.append({"clients": None, "cookies_file": cookies_file, "browser": None})
        strategies.append({"clients": "android,ios,mweb,web", "cookies_file": cookies_file, "browser": None})
    else:
        # Without cookies, try detected local browsers first with standard client
        for b in browsers:
            strategies.append({"clients": None, "cookies_file": None, "browser": b})
            strategies.append({"clients": "android,ios,mweb,web", "cookies_file": None, "browser": b})
        # Standard unauthenticated attempt
        strategies.append({"clients": None, "cookies_file": None, "browser": None})
        # Mobile player client bypass
        strategies.append({"clients": "android,ios,mweb,web", "cookies_file": None, "browser": None})

    # Fallback clients
    strategies.append({"clients": "ios,android", "cookies_file": cookies_file, "browser": None})
    strategies.append({"clients": "mweb", "cookies_file": cookies_file, "browser": None})

    info = None
    last_err = ""
    used_strategy = None

    for strat in strategies:
        cmd = [
            ytdlp,
            "--skip-download",
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            *build_ytdlp_extractor_args(strat.get("clients")),
            *build_ytdlp_auth_args(cookies_file=strat.get("cookies_file"), browser=strat.get("browser")),
            clean_url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except Exception as exc:
            raise ValueError(f"执行 yt-dlp 失败，请确认系统已正确安装: {exc}")

        if proc.returncode == 0 and stdout:
            try:
                candidate_info = json.loads(stdout.decode(errors="replace"))
                info = candidate_info
                used_strategy = strat
                # If candidate contains subtitles or automatic captions, we can stop
                if candidate_info.get("subtitles") or candidate_info.get("automatic_captions"):
                    break
            except Exception as exc:
                last_err = f"解析 YouTube 视频信息响应失败: {exc}"
        else:
            err_msg = stderr.decode(errors="replace").strip()
            err_line = err_msg.splitlines()[-1] if err_msg else "未知错误"
            last_err = err_line
            logger.debug(f"yt-dlp strategy {strat} failed: {err_line}")

    title = ""
    channel = ""
    cues = []

    if info:
        title = (info.get("title") or "YouTube Video").strip()
        channel = (info.get("channel") or info.get("uploader") or "YouTube").strip()

        subtitles = info.get("subtitles") or {}
        auto_captions = info.get("automatic_captions") or {}

        # Locate English subtitle track (prefer manual subtitles, then auto-captions)
        chosen_lang = None
        formats = None
        priority_langs = ["en", "en-US", "en-GB", "en-CA", "en-AU", "en-IE", "en-NZ", "en-orig"]

        # 1. Check manual subtitles
        for lang in priority_langs:
            if lang in subtitles:
                chosen_lang = lang
                formats = subtitles[lang]
                break
        if not formats:
            for k, v in subtitles.items():
                if k.startswith("en") and not re.search(r"en-[a-z]{2,}", k):
                    chosen_lang = k
                    formats = v
                    break

        # 2. Check automatic captions if manual not available
        if not formats:
            for lang in priority_langs:
                if lang in auto_captions:
                    chosen_lang = lang
                    formats = auto_captions[lang]
                    break
        if not formats:
            for k, v in auto_captions.items():
                if k.startswith("en"):
                    chosen_lang = k
                    formats = v
                    break

        if formats:
            # Pick the best format: prefer vtt, then json3, then srt
            format_by_ext = {f.get("ext"): f for f in formats if isinstance(f, dict)}
            chosen_fmt = (
                format_by_ext.get("vtt")
                or format_by_ext.get("json3")
                or format_by_ext.get("srt")
                or (formats[0] if formats else None)
            )

            # Try direct HTTP fetch first if URL is available
            if chosen_fmt and chosen_fmt.get("url"):
                sub_url = chosen_fmt["url"]
                ext = chosen_fmt.get("ext", "vtt")
                try:
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http_client:
                        r = await http_client.get(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                        if r.status_code == 200:
                            if ext == "json3" or sub_url.endswith("fmt=json3"):
                                cues = parse_json3(r.json())
                            elif ext == "srt" or sub_url.endswith("fmt=srt"):
                                cues = parse_srt(r.text)
                            else:
                                cues = parse_vtt(r.text)
                except Exception as exc:
                    logger.info(f"Direct subtitle URL fetch failed: {exc}, will fallback to yt-dlp file download")

            # If direct fetch did not yield cues, download subtitle file via yt-dlp directly
            if not cues:
                temp_dir = Path(tempfile.mkdtemp(prefix="ytdl_sub_"))
                try:
                    dl_cmd = [
                        ytdlp,
                        "--skip-download",
                        "--write-subs",
                        "--write-auto-subs",
                        "--sub-langs", chosen_lang or "en",
                        "--convert-subs", "vtt",
                        "--no-playlist",
                        *build_ytdlp_extractor_args(
                            used_strategy.get("clients", "android,ios,mweb,web") if used_strategy else "android,ios,mweb,web"
                        ),
                        *build_ytdlp_auth_args(
                            cookies_file=used_strategy.get("cookies_file") if used_strategy else cookies_file,
                            browser=used_strategy.get("browser") if used_strategy else None,
                        ),
                        "-o", str(temp_dir / "video.%(ext)s"),
                        clean_url,
                    ]
                    proc = await asyncio.create_subprocess_exec(
                        *dl_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    vtt_files = list(temp_dir.glob("*.vtt"))
                    if vtt_files:
                        content = vtt_files[0].read_text(encoding="utf-8", errors="replace")
                        cues = parse_vtt(content)
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

    # Step 2: If cues not obtained yet via yt-dlp, invoke youtube-transcript-api fallback
    if not cues and vid:
        logger.info(f"yt-dlp did not produce cues; attempting youtube-transcript-api fallback for video {vid}...")
        try:
            transcript_cues = await asyncio.to_thread(fetch_subtitles_via_transcript_api, vid)
            if transcript_cues:
                cues = transcript_cues
                logger.info(f"Successfully retrieved {len(cues)} cues via youtube-transcript-api fallback.")
                if not title or title == "YouTube Video":
                    oembed_title, oembed_author = await fetch_youtube_oembed_metadata(clean_url)
                    title = oembed_title or title
                    channel = oembed_author or channel
        except Exception as exc:
            logger.warning(f"youtube-transcript-api fallback failed: {exc}")
            if not last_err:
                last_err = str(exc)

    if not cues:
        # Check if video was found but no English subtitles
        if info and not (info.get("subtitles") or info.get("automatic_captions")):
            raise ValueError(
                "该 YouTube 视频未包含可用英文字幕或自动生成英文文稿 (No English subtitles found)。\n\n"
                "💡 提示：若您有该视频的英文文稿，可在弹窗中选择「直接粘贴文章内容」一键导入排版。"
            )
        raise ValueError(build_graceful_failure_message(last_err))

    if not title:
        title = "YouTube Video"
    if not channel:
        channel = "YouTube"

    return title, cues, channel


async def process_youtube_subtitles_to_sentences(cues: List[Dict]) -> List[str]:
    """Clean, deduplicate, and stitch raw subtitle cues into complete punctuated sentences."""
    deduped = deduplicate_rolling_cues(cues)
    if not deduped:
        return []

    combined_text = " ".join(c["text"] for c in deduped)
    if has_substantive_punctuation(combined_text):
        logger.info("Detected pre-existing punctuation in YouTube subtitles, stitching via punctuation boundaries.")
        sentences = stitch_punctuated_segments(deduped)
    else:
        logger.info("Detected unpunctuated/auto YouTube subtitles, restoring punctuation via LLM engine.")
        sentences = await stitch_unpunctuated_segments_with_llm(deduped)

    # Filter out empty, purely numeric, or single-word fragments, and ensure optimal length
    valid_sentences = []
    for s in sentences:
        s_clean = clean_disfluencies(s)
        if not s_clean or len(s_clean.split()) < 2:
            continue
        if not re.search(r"[a-zA-Z]", s_clean):
            continue
        for sub in split_long_sentence(s_clean, target_min=8, target_max=22, hard_cap=28):
            sub = sub.strip()
            if sub and len(sub.split()) >= 2 and re.search(r"[a-zA-Z]", sub):
                valid_sentences.append(sub)

    return valid_sentences


def group_sentences_into_paragraphs(sentences: List[str], sentences_per_para: int = 3) -> List[str]:
    """Group individual sentences into balanced narrative paragraphs for textbook layout."""
    paragraphs = []
    for i in range(0, len(sentences), sentences_per_para):
        para = " ".join(sentences[i:i + sentences_per_para]).strip()
        if para:
            paragraphs.append(para)
    return paragraphs


def parse_pasted_transcript(raw_text: str) -> Tuple[Optional[str], List[str]]:
    """Parse raw pasted transcript text (which may contain YouTube timestamps, WebVTT,
    or SRT formatting) into clean, stitched paragraphs suitable for textbook rendering.
    Returns (suggested_title, paragraphs).
    """
    if not raw_text or not raw_text.strip():
        return None, []

    text = raw_text.strip()
    cues = []

    # 1. Check if WebVTT
    if "WEBVTT" in text:
        cues = parse_vtt(text)

    # 2. Check if SRT
    if not cues and re.search(r"\d+\s*\n\s*(?:(?:\d{1,2}:)?\d{2}:\d{2}[,\.]\d{3})\s*-->", text):
        cues = parse_srt(text)

    # 3. Check for YouTube transcript format (lines with 0:00 or timestamps)
    time_line_pat = re.compile(r"^(?:(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{3})?)$")
    inline_time_pat = re.compile(r"^(?:(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{3})?)\s+")

    def _parse_time_str(ts: str) -> float:
        parts = [int(p) for p in re.findall(r"\d+", ts)]
        if len(parts) == 2:
            return float(parts[0] * 60 + parts[1])
        elif len(parts) >= 3:
            return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
        return 0.0

    if not cues:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        has_timestamps = any(time_line_pat.match(ln) or inline_time_pat.match(ln) for ln in lines)
        if has_timestamps:
            curr_sec = 0.0
            cleaned_cues = []
            for ln in lines:
                m_timeline = time_line_pat.match(ln)
                if m_timeline:
                    curr_sec = _parse_time_str(ln)
                    continue

                m_inline = inline_time_pat.match(ln)
                if m_inline:
                    curr_sec = _parse_time_str(m_inline.group(0))
                    ln_clean = inline_time_pat.sub("", ln).strip()
                else:
                    ln_clean = ln.strip()

                cleaned = clean_cue_text(ln_clean)
                if cleaned:
                    cleaned_cues.append({
                        "start": float(curr_sec),
                        "end": float(curr_sec) + 2.0,
                        "text": cleaned,
                    })
                    curr_sec += 2.0
            cues = cleaned_cues

    suggested_title = None
    if cues:
        deduped = deduplicate_rolling_cues(cues)
        if deduped:
            all_text = " ".join(c["text"] for c in deduped)
            if has_substantive_punctuation(all_text):
                sentences = stitch_punctuated_segments(deduped)
            else:
                sentences = rule_based_segment_stitch(deduped)

            valid_sentences = []
            for s in sentences:
                s_clean = clean_disfluencies(s)
                for sub in split_long_sentence(s_clean, target_min=8, target_max=22, hard_cap=28):
                    sub = sub.strip()
                    if sub and len(sub.split()) >= 2 and re.search(r"[a-zA-Z]", sub):
                        valid_sentences.append(sub)

            if valid_sentences:
                first_sent = valid_sentences[0]
                first_sent_clean = re.sub(r'[.!?\"\'“”]+$', '', first_sent).strip()
                if len(first_sent_clean) > 60:
                    first_sent_clean = first_sent_clean[:57] + "..."
                suggested_title = first_sent_clean
                paras = group_sentences_into_paragraphs(valid_sentences)
                return suggested_title, paras

    # Fallback to standard paragraphs if not timestamped transcript format
    clean_text = clean_disfluencies(text)
    raw_paras = [p.strip() for p in re.split(r"\n\s*\n", clean_text) if p.strip()]
    if not raw_paras:
        raw_paras = [clean_text]

    all_sentences = []
    final_paragraphs = []

    for p in raw_paras:
        cleaned_p = clean_cue_text(p)
        if not cleaned_p or not re.search(r"[a-zA-Z]", cleaned_p):
            continue

        if has_substantive_punctuation(cleaned_p):
            sim_cues = [{"start": 0.0, "end": 1.0, "text": cleaned_p}]
            sents = stitch_punctuated_segments(sim_cues)
        else:
            sim_cues = [{"start": float(i), "end": float(i) + 0.5, "text": chunk}
                        for i, chunk in enumerate(cleaned_p.splitlines()) if chunk.strip()]
            if not sim_cues:
                sim_cues = [{"start": 0.0, "end": 1.0, "text": cleaned_p}]
            sents = rule_based_segment_stitch(sim_cues)

        p_sents = []
        for s in sents:
            s_clean = clean_disfluencies(s)
            for sub in split_long_sentence(s_clean, target_min=8, target_max=22, hard_cap=28):
                sub = sub.strip()
                if sub and len(sub.split()) >= 2 and re.search(r"[a-zA-Z]", sub):
                    p_sents.append(sub)
                    all_sentences.append(sub)

        if p_sents:
            if len(p_sents) > 4:
                final_paragraphs.extend(group_sentences_into_paragraphs(p_sents, sentences_per_para=3))
            else:
                final_paragraphs.append(" ".join(p_sents))

    if all_sentences:
        first_line = all_sentences[0]
        first_line_clean = re.sub(r'[.!?\"\'“”]+$', '', first_line).strip()
        if len(first_line_clean) > 60:
            first_line_clean = first_line_clean[:57] + "..."
        suggested_title = first_line_clean or "Pasted Content"
        paragraphs = final_paragraphs if final_paragraphs else group_sentences_into_paragraphs(all_sentences)
        return suggested_title, paragraphs

    return suggested_title, []


class YouTubeImportResult(tuple):
    """Result tuple for YouTube and Web import pipelines.
    Acts as a 5-tuple (title, pdf_bytes, desc, total_pages, safe_filename) for full
    backward-compatibility with existing unpacking code, while carrying extra
    media_path and sentence_timestamps attributes.
    """
    def __new__(
        cls,
        title: str,
        pdf_bytes: bytes,
        desc: str,
        total_pages: int,
        safe_filename: str,
        media_path: Optional[str] = None,
        sentence_timestamps: Optional[List[Dict]] = None,
    ):
        return super().__new__(cls, (title, pdf_bytes, desc, total_pages, safe_filename))

    def __init__(
        self,
        title: str,
        pdf_bytes: bytes,
        desc: str,
        total_pages: int,
        safe_filename: str,
        media_path: Optional[str] = None,
        sentence_timestamps: Optional[List[Dict]] = None,
    ):
        self.title = title
        self.pdf_bytes = pdf_bytes
        self.desc = desc
        self.total_pages = total_pages
        self.safe_filename = safe_filename
        self.media_path = str(media_path) if media_path else None
        self.sentence_timestamps = sentence_timestamps or []


def align_sentences_with_cues(sentences: List[str], cues: List[Dict]) -> List[Dict]:
    """Align stitched sentences with original subtitle cue timestamps.
    Returns list of dicts: [{'text': sent, 'start_time': float, 'end_time': float}, ...]
    """
    if not sentences:
        return []
    if not cues:
        return [{"text": s, "start_time": 0.0, "end_time": 0.0} for s in sentences]

    timed_tokens = []
    for cue in cues:
        raw_text = clean_disfluencies(cue.get("text", ""), capitalize_first=False)
        words = re.findall(r"[a-zA-Z0-9']+", raw_text.lower())
        if not words:
            continue
        start = float(cue.get("start", 0.0))
        end = float(cue.get("end", start + 1.0))
        dur = max(end - start, 0.1)
        w_dur = dur / len(words)
        for i, w in enumerate(words):
            timed_tokens.append({
                "word": w,
                "start": start + i * w_dur,
                "end": start + (i + 1) * w_dur,
            })

    if not timed_tokens:
        return [{"text": s, "start_time": 0.0, "end_time": 0.0} for s in sentences]

    aligned = []
    token_idx = 0
    total_tokens = len(timed_tokens)
    last_end_time = float(timed_tokens[0]["start"])

    for s in sentences:
        s_words = re.findall(r"[a-zA-Z0-9']+", s.lower())
        if not s_words:
            aligned.append({
                "text": s,
                "start_time": round(last_end_time, 2),
                "end_time": round(last_end_time + 2.0, 2),
            })
            last_end_time += 2.0
            continue

        first_match_idx = None
        search_limit = min(token_idx + 80, total_tokens)
        for cand_idx in range(token_idx, search_limit):
            if timed_tokens[cand_idx]["word"] == s_words[0]:
                first_match_idx = cand_idx
                break

        if first_match_idx is None and len(s_words) > 1:
            for cand_idx in range(token_idx, search_limit):
                if timed_tokens[cand_idx]["word"] in s_words[:3]:
                    first_match_idx = cand_idx
                    break

        if first_match_idx is not None:
            token_idx = first_match_idx
            start_time = timed_tokens[first_match_idx]["start"]
        else:
            start_time = last_end_time

        curr_ptr = token_idx
        last_match_idx = token_idx
        for w in s_words:
            step_limit = min(curr_ptr + 25, total_tokens)
            for search_idx in range(curr_ptr, step_limit):
                if timed_tokens[search_idx]["word"] == w:
                    last_match_idx = search_idx
                    curr_ptr = search_idx + 1
                    break

        end_time = timed_tokens[last_match_idx]["end"]
        token_idx = min(last_match_idx + 1, total_tokens)

        if start_time < last_end_time - 1.0:
            start_time = last_end_time
        if end_time <= start_time:
            end_time = start_time + max(len(s_words) * 0.35, 1.2)

        last_end_time = end_time
        aligned.append({
            "text": s,
            "start_time": round(max(0.0, start_time), 2),
            "end_time": round(max(0.1, end_time), 2),
        })

    return aligned


async def download_youtube_media(
    url: str,
    output_stem: str,
    target_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Download video (or audio fallback) of YouTube video using yt-dlp.
    Prefers 720p/480p MP4 for fast download and universal browser playback.
    Saves to target_dir (default: MEDIA_DIR).
    Returns Path to downloaded media file or None if download failed.
    """
    dest_dir = target_dir or MEDIA_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = get_ytdlp_executable()
    vid = extract_youtube_video_id(url)
    clean_url = f"https://www.youtube.com/watch?v={vid}" if vid else url.strip()

    # Check if a finished media file already exists with this stem
    for ext in [".mp4", ".m4a", ".webm", ".mkv"]:
        existing = dest_dir / f"{output_stem}{ext}"
        if existing.is_file() and existing.stat().st_size > 1024:
            logger.info(f"Using pre-existing media file: {existing}")
            return existing

    cookies_file = get_youtube_cookies_file()
    browsers = detect_available_browsers() if not cookies_file else []

    strategies = []
    if cookies_file:
        strategies.append({"clients": None, "cookies_file": cookies_file, "browser": None})
        strategies.append({"clients": "android,ios,mweb,web", "cookies_file": cookies_file, "browser": None})
    else:
        for b in browsers:
            strategies.append({"clients": None, "cookies_file": None, "browser": b})
        strategies.append({"clients": "android,ios,mweb,web", "cookies_file": None, "browser": None})
        strategies.append({"clients": None, "cookies_file": None, "browser": None})

    output_tpl = str(dest_dir / f"{output_stem}.%(ext)s")

    for strat in strategies:
        cmd = [
            ytdlp,
            "--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--socket-timeout", "30",
            *build_ytdlp_extractor_args(strat.get("clients")),
            *build_ytdlp_auth_args(cookies_file=strat.get("cookies_file"), browser=strat.get("browser")),
            "-o", output_tpl,
            clean_url,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                candidates = list(dest_dir.glob(f"{output_stem}.*"))
                if candidates:
                    mp4_cands = [c for c in candidates if c.suffix.lower() == ".mp4"]
                    media_path = mp4_cands[0] if mp4_cands else candidates[0]
                    logger.info(f"Successfully downloaded YouTube video: {media_path} ({media_path.stat().st_size} bytes)")
                    return media_path
            else:
                logger.debug(f"yt-dlp video download strategy failed: {stderr.decode(errors='replace')[:200]}")
        except Exception as exc:
            logger.warning(f"Error running yt-dlp video download: {exc}")

    # Fallback to audio-only if video failed
    logger.info("Video stream download failed or unavailable, attempting best audio download fallback...")
    for strat in strategies:
        cmd = [
            ytdlp,
            "--extract-audio",
            "--audio-format", "m4a",
            "--no-playlist",
            "--no-warnings",
            "--socket-timeout", "30",
            *build_ytdlp_extractor_args(strat.get("clients")),
            *build_ytdlp_auth_args(cookies_file=strat.get("cookies_file"), browser=strat.get("browser")),
            "-o", output_tpl,
            clean_url,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                candidates = list(dest_dir.glob(f"{output_stem}.*"))
                if candidates:
                    media_path = candidates[0]
                    logger.info(f"Successfully downloaded YouTube audio fallback: {media_path}")
                    return media_path
        except Exception as exc:
            logger.warning(f"Error running yt-dlp audio fallback: {exc}")

    logger.warning(f"Could not download media for {url}, proceeding without local media file.")
    return None


async def process_youtube_import(
    url: str,
    custom_title: Optional[str] = None,
    download_media: bool = True,
) -> YouTubeImportResult:
    """End-to-end import pipeline for YouTube videos:
    1. Fetch subtitles and metadata
    2. Clean and stitch into complete punctuated sentences
    3. Generate custom textbook PDF
    4. Download video (or audio fallback)
    5. Align sentence timestamps
    6. Return YouTubeImportResult (5-tuple with media_path and sentence_timestamps).
    """
    video_id = extract_youtube_video_id(url) or "video"
    raw_title, cues, channel = await fetch_youtube_metadata_and_subtitles(url)

    final_title = (custom_title or raw_title).strip()
    final_title = normalize_typography(final_title)

    sentences = await process_youtube_subtitles_to_sentences(cues)
    if not sentences:
        raise ValueError("从该视频提取的英文字幕有效句子为空。")

    sentence_timestamps = align_sentences_with_cues(sentences, cues)

    paragraphs = group_sentences_into_paragraphs(sentences)
    desc = f"YouTube 视频字幕 · {channel} (共 {len(sentences)} 句)"

    # Build A4 PDF
    pdf_bytes, total_pages = build_article_pdf(final_title, paragraphs, url=url)

    # Map each sentence to its corresponding PDF page number
    current_words = 0
    current_page = 1
    page_paras = 0
    para_to_page = {}
    for p_idx, p in enumerate(paragraphs):
        w_count = len(p.split())
        if current_words + w_count > 240 and page_paras >= 1:
            current_page += 1
            current_words = w_count
            page_paras = 1
        else:
            current_words += w_count
            page_paras += 1
        para_to_page[p_idx] = current_page

    for s_idx, st in enumerate(sentence_timestamps):
        p_idx = s_idx // 3
        st["page"] = para_to_page.get(p_idx, 1)

    # Safe filename for custom_books directory
    clean_stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fa5]", "_", final_title)[:35].strip("_-")
    if not clean_stem:
        clean_stem = "youtube_video"
    safe_filename = f"yt_{clean_stem}_{video_id}.pdf"
    file_stem = f"yt_{clean_stem}_{video_id}"

    media_path = None
    if download_media:
        try:
            media_path = await download_youtube_media(url, output_stem=file_stem)
        except Exception as exc:
            logger.warning(f"Failed to download YouTube media for {url}: {exc}")

    return YouTubeImportResult(
        final_title,
        pdf_bytes,
        desc,
        total_pages,
        safe_filename,
        media_path=str(media_path) if media_path else None,
        sentence_timestamps=sentence_timestamps,
    )
