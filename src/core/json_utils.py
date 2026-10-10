"""Robust extraction of JSON from LLM prose.

Both the practice generator and the translation/gloss services ask a local model
for JSON and then had to cope with fenced blocks, ``<think>`` preambles, trailing
commas and chit-chat around the payload. The previous approach — "grab everything
from the first ``{`` to the last ``}``" — is greedy, so any stray brace in
*trailing* prose swallowed unrelated text and produced an unparseable string:

    {...valid json...}

    (Note: {count} = 1 above.)

became ``{...valid json...}\\n\\n(Note: {count}`` and every caller fell back to a
slower path (a per-sentence retry for translation, a hard failure for the
practice generator) even though the model had answered correctly.

``extract_json`` scans for the first balanced JSON value, respecting string
literals and escapes, and returns it without depending on what follows.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")
_THINK_RE = re.compile(r"<think[\s\S]*?</think\s*>", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([\]}])")


def _strip_wrappers(text: str) -> str:
    """Remove reasoning blocks and prefer a fenced block when one exists."""
    cleaned = _THINK_RE.sub("", text or "")
    fenced = _FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    return cleaned.strip()


def _first_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """Return the first balanced ``open_ch``..``close_ch`` span, ignoring strings."""
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def extract_json(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """Extract the first JSON value from model output.

    Returns ``(value, raw_json_text)``. ``value`` is ``None`` when nothing
    parseable was found.
    """
    if not text:
        return None, None
    cleaned = _strip_wrappers(text)

    candidates = []
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        span = _first_balanced(cleaned, open_ch, close_ch)
        if span:
            candidates.append(span)

    if not candidates:
        return None, None

    # Prefer object/array by whichever appears first in the text.
    candidates.sort(key=cleaned.find)

    for span in candidates:
        for attempt in (span, _TRAILING_COMMA_RE.sub(r"\1", span)):
            try:
                return json.loads(attempt), attempt
            except (ValueError, TypeError):
                continue

    return None, None
