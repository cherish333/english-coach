"""Lightweight, dependency-free English phonetic comparison.

The pronunciation evaluator used to compare ASR *spelling* with difflib, which
means a learner who says "sink" instead of "think" scored 96/100 because the two
strings are 66% similar.  Spelling similarity is not pronunciation similarity:
"think"/"sink", "three"/"free", "very"/"wary" and "live"/"leave" are exactly the
minimal pairs Chinese learners struggle with, and they were all being waved
through.

This module converts words into a coarse English phonetic key so the evaluator
can decide whether the learner produced the *right phonemes*, and — when they
did not — which systematic L1 transfer pattern is responsible.

Design constraints:
  * Pure standard library, no network, no model download.
  * Cheap enough to run per word on every utterance.
  * Deliberately conservative: only codes distinctions that are phonemically
    contrastive in English, so it never punishes a valid alternative
    pronunciation (e.g. schedule /sk-/ vs /ʃ-/).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Word extraction / normalisation
# ---------------------------------------------------------------------------

CONTRACTION_MAP: Dict[str, str] = {
    "it's": "it is",
    "i'm": "i am",
    "you're": "you are",
    "he's": "he is",
    "she's": "she is",
    "we're": "we are",
    "they're": "they are",
    "that's": "that is",
    "what's": "what is",
    "there's": "there is",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "won't": "will not",
    "can't": "cannot",
    "couldn't": "could not",
    "shouldn't": "should not",
    "wouldn't": "would not",
    "haven't": "have not",
    "hasn't": "has not",
    "hadn't": "had not",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "let's": "let us",
}

EXPANDED_TO_CONTRACTION: Dict[str, str] = {v: k for k, v in CONTRACTION_MAP.items()}

# Connected-speech reductions. These are *correct* natural speech, not errors: a
# learner who says "I wanna go" has produced better English than one who says
# "I want to go" word by word, so the grader must accept them. Keys are the
# reduced spelling an ASR tends to emit; values are the canonical words.
REDUCTION_MAP: Dict[str, str] = {
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "got to",
    "kinda": "kind of",
    "sorta": "sort of",
    "outta": "out of",
    "alotta": "a lot of",
    "dunno": "do not know",
    "lemme": "let me",
    "gimme": "give me",
    "cuz": "because",
    "cos": "because",
    "cause": "because",
    "yeah": "yes",
    "yep": "yes",
    "nope": "no",
}

# Reverse lookup: canonical word -> accepted reduction.
CANONICAL_TO_REDUCTION: Dict[str, str] = {v: k for k, v in REDUCTION_MAP.items()}

# Whole-phrase reductions. Natural speech drops auxiliaries that a word-by-word
# comparison would report as "missing" ("I have got to go" -> "I gotta go"),
# which would punish the learner for speaking *more* natively. Longest phrases
# are matched first.
PHRASE_REDUCTIONS: Dict[str, str] = {
    "i have got to": "i gotta",
    "i have got a": "i gotta",
    "have got to": "gotta",
    "has got to": "gotta",
    "i am going to": "i'm gonna",
    "you are going to": "you're gonna",
    "we are going to": "we're gonna",
    "they are going to": "they're gonna",
    "going to": "gonna",
    "want to": "wanna",
    "got to": "gotta",
    "kind of": "kinda",
    "sort of": "sorta",
    "out of": "outta",
    "a lot of": "alotta",
    "let me": "lemme",
    "give me": "gimme",
    "have to": "hafta",
    "has to": "hasta",
    "do not know": "dunno",
    "because": "cuz",
} | {v: k for k, v in REDUCTION_MAP.items() if " " in k}


def clean_word(word: str) -> str:
    """Strip surrounding punctuation, normalise quotes, and lowercase."""
    word = word.replace("\u2019", "'").replace("\u2018", "'").lower()
    return re.sub(r"^[^\w']+|[^\w']+$", "", word).strip(".,!?:;()")


def is_english_word(token: str) -> bool:
    """True when the token looks like an English lexical word."""
    if not token:
        return False
    return bool(re.search(r"[a-z]", token)) and not re.search(r"[\u4e00-\u9fff]", token)


# ---------------------------------------------------------------------------
# Phonetic key
# ---------------------------------------------------------------------------

_VOWELS = set("aeiouy")

# Ordered longest-first so digraphs win over single letters.
_DIGRAPHS: Tuple[Tuple[str, str], ...] = (
    ("ough", "O"),
    ("augh", "O"),
    ("tion", "Sn"),
    ("sion", "Sn"),
    ("ture", "Cr"),
    ("ck", "K"),
    ("ch", "C"),
    ("sh", "X"),
    ("ph", "F"),
    ("gh", "G"),
    ("th", "0"),
    ("wh", "W"),
    ("qu", "KW"),
    ("ng", "N"),
    ("nk", "NK"),
    ("kn", "N"),
    ("wr", "R"),
    ("gn", "N"),
    ("ps", "S"),
    ("mb", "M"),
)

_SINGLES: Dict[str, str] = {
    "b": "B",
    "c": "K",
    "d": "D",
    "f": "F",
    "g": "G",
    "h": "H",
    "j": "J",
    "k": "K",
    "l": "L",
    "m": "M",
    "n": "N",
    "p": "P",
    "q": "K",
    "r": "R",
    "s": "S",
    "t": "T",
    "v": "V",
    "w": "W",
    "x": "KS",
    "z": "Z",
}

# Tokens that only make sense as whole words.
_IRREGULAR_KEYS: Dict[str, str] = {
    "the": "D",
    "a": "A",
    "an": "AN",
    "to": "T",
    "of": "V",
    "one": "WAN",
    "two": "T",
    "who": "H",
    "what": "W",
    "where": "W",
    "when": "W",
    "why": "Y",
    "how": "H",
    "you": "Y",
    "your": "YR",
    "we": "WE",
    "he": "HE",
    "she": "XE",
    "be": "BE",
    "by": "BY",
    "my": "MY",
    "me": "ME",
    "no": "NO",
    "so": "SO",
    "do": "DO",
    "go": "GO",
    "are": "R",
    "or": "R",
    "for": "FR",
    "four": "FR",
    "were": "WR",
    "where's": "WR",
    "their": "0R",
    "there": "0R",
    "they're": "0R",
    "right": "RYT",
    "write": "RYT",
    "read": "RED",
    "said": "SED",
    "says": "SEZ",
    "one's": "WANZ",
    "once": "WANS",
    "through": "0RU",
    "though": "0O",
    "thought": "0OT",
    "thorough": "0URO",
    "know": "NO",
    "knew": "NU",
    "new": "NU",
    "grew": "GRU",
    "would": "WUD",
    "wood": "WUD",
    "could": "KUD",
    "should": "XUD",
    "buy": "BY",
    "bye": "BY",
    "live": "LIV",
    "leave": "LEV",
}

# Words whose "th" is not /θ/ or /ð/ cannot be handled by the digraph table
# alone; these overrides keep the key honest.
_TH_OVERRIDES: Dict[str, str] = {
    "thomas": "TOM",
    "thames": "TEM",
    "eight": "AT",
    "eighth": "AT0",
    "height": "HYT",
    "heights": "HYTS",
    "straight": "STRAT",
    "straighter": "STRATR",
    "neighbour": "NABR",
    "neighbor": "NABR",
    "weigh": "WA",
    "weight": "WAT",
    "throughout": "0RUOT",
    "thoroughly": "0URULY",
    "threat": "0RET",
    "thread": "0RED",
    "three": "0RE",
    "third": "0RD",
    "thirsty": "0RSTY",
    "thousand": "0UZND",
    "thank": "0ANK",
    "thanks": "0ANKS",
    "thing": "0ING",
    "think": "0INK",
    "this": "0IS",
    "that": "0AT",
    "these": "0EZ",
    "those": "0OZ",
    "them": "0EM",
    "then": "0EN",
    "than": "0AN",
    "there": "0ER",
    "their": "0ER",
    "they": "0A",
    "the": "0E",
    "mother": "M0ER",
    "father": "F0ER",
    "brother": "BR0ER",
    "other": "0ER",
    "another": "AN0ER",
    "weather": "WE0ER",
    "whether": "WE0ER",
    "together": "TEGE0ER",
    "north": "NOR0",
    "south": "SAU0",
    "both": "BO0",
    "with": "WI0",
    "without": "WI0AUT",
    "something": "SAM0ING",
    "anything": "ENY0ING",
    "nothing": "NA0ING",
    "everything": "EVRI0ING",
    "healthy": "HEL0Y",
    "wealthy": "WEL0Y",
    "months": "MUN0S",
    "month": "MUN0",
    "mouth": "MAU0",
    "truth": "TRU0",
    "breath": "BRE0",
    "breathe": "BRE0",
    "clothes": "KLOZ",
    "clothing": "KLO0ING",
}


def _collapse_repeats(key: str) -> str:
    """Collapse runs of the same phoneme symbol (accommodate -> acomodate)."""
    out: List[str] = []
    for ch in key:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def phonetic_key(word: str) -> str:
    """Return a coarse phonetic key for an English word.

    The key is a rough, English-specific phonemic sketch: enough to tell
    ``think`` from ``sink`` and ``three`` from ``free``, while staying tolerant
    of spelling variation that does not change pronunciation
    (``accommodate`` / ``acommodate``).
    """
    token = clean_word(word)
    if not token:
        return ""
    if not is_english_word(token):
        # Non-English input cannot be phonetically compared; fall back to the
        # literal token so callers can still detect "wrong language entirely".
        return token

    if token in _TH_OVERRIDES:
        return _TH_OVERRIDES[token]

    if token in _IRREGULAR_KEYS:
        # Still prefer the algorithmic key when the irregular entry is much
        # shorter than the word, which usually signals an inflected form.
        base = _IRREGULAR_KEYS[token]
        if len(token) - len(base) <= 2:
            return base

    text = token
    # Silent final 'e' (make -> mak), but keep it when it is the only vowel-ish
    # carrier of a very short word (be, he, she).
    if len(text) > 2 and text.endswith("e") and text[-2] not in _VOWELS:
        text = text[:-1]
    # Plural / past-tense endings have predictable pronunciations.
    suffix = ""
    if len(text) > 3 and text.endswith("es") and text[-3] in "sxzcg":
        text, suffix = text[:-2], "EZ"
    elif len(text) > 3 and text.endswith("ed") and text[-3] in "td":
        text, suffix = text[:-2], "ED"
    elif len(text) > 2 and text.endswith("s") and text[-2] not in "su":
        text, suffix = text[:-1], "S"

    key: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for digraph, code in _DIGRAPHS:
            if text.startswith(digraph, i):
                # 'ng' inside a longer cluster already covered; 'ps'/'mb' only
                # silent at the start / end of a word.
                if digraph == "ps" and i != 0:
                    continue
                if digraph == "mb" and i != n - 2:
                    continue
                if digraph == "kn" and i != 0:
                    continue
                if digraph == "wr" and i != 0:
                    continue
                if digraph == "gn" and i != 0:
                    continue
                key.append(code)
                i += len(digraph)
                matched = True
                break
        if matched:
            continue

        ch = text[i]
        if ch in _VOWELS:
            # Collapse vowel runs; silent final 'e' already stripped.
            if not key or key[-1] not in ("A", "E", "I", "O", "U"):
                key.append("A")
            i += 1
            continue

        code = _SINGLES.get(ch)
        if code is None:
            i += 1
            continue
        key.append(code)
        i += 1

    return _collapse_repeats("".join(key)) + suffix


# ---------------------------------------------------------------------------
# L1-transfer error signatures
# ---------------------------------------------------------------------------

# A pair predicate receives the two lowercased tokens. severity:
# 1.0 = meaning-changing contrast, 0.5 = accent-coloured but understandable.
_PAIR_RULES: List[Tuple[str, float, str]] = []
_PAIR_FUNCS: Dict[str, object] = {}


def _rule(pid: str, severity: float, label: str):
    def deco(fn):
        _PAIR_RULES.append((pid, severity, label))
        _PAIR_FUNCS[pid] = fn
        return fn

    return deco


@_rule("th_to_s", 1.0, "咬舌音 /θ/ 被读成 /s/（think → sink）")
def _th_to_s(a: str, b: str) -> bool:
    return (
        ("th" in a and "s" in b and b.replace("s", "th") == a)
        or ("th" in b and "s" in a and a.replace("s", "th") == b)
    )


@_rule("th_to_f", 1.0, "咬舌音 /θ/ 被读成 /f/（three → free）")
def _th_to_f(a: str, b: str) -> bool:
    return (
        ("th" in a and "f" in b and b.replace("f", "th") == a)
        or ("th" in b and "f" in a and a.replace("f", "th") == b)
    )


@_rule("v_to_w", 1.0, "/v/ 与 /w/ 混淆（very → wary）")
def _v_to_w(a: str, b: str) -> bool:
    return a.replace("v", "w") == b or b.replace("v", "w") == a


@_rule("l_to_n", 1.0, "/l/ 与 /n/ 混淆（light → night）")
def _l_to_n(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    return a.replace("l", "n") == b or b.replace("l", "n") == a


@_rule("final_consonant_drop", 0.7, "词尾辅音脱落（and → an）")
def _final_drop(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 2:
        return False
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    if not longer or not shorter:
        return False
    return longer.startswith(shorter) and longer[len(shorter):] in (
        "d", "t", "s", "z", "k", "p", "b", "g", "m", "n", "l", "r", "f", "v",
    )


@_rule("short_long_vowel", 0.7, "长短元音不分（live → leave, ship → sheep）")
def _short_long(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    for short, long_ in (("i", "ee"), ("i", "ea"), ("u", "oo"), ("a", "ar"), ("o", "or")):
        if a.replace(short, long_, 1) == b or b.replace(short, long_, 1) == a:
            return True
    stripped_a = re.sub(r"(.)\1", r"\1", a)
    stripped_b = re.sub(r"(.)\1", r"\1", b)
    return stripped_a != stripped_b and stripped_a.replace("i", "ee", 1) == stripped_b


@_rule("r_l_confusion", 0.5, "/r/ 与 /l/ 混淆")
def _r_l(a: str, b: str) -> bool:
    return a.replace("r", "l") == b or b.replace("r", "l") == a


@_rule("ed_ending", 0.5, "过去式 -ed 结尾吞音（worked → work）")
def _ed_ending(a: str, b: str) -> bool:
    """Detect a swallowed past-tense ending: worked → work, wanted → want."""
    if not (a.endswith("ed") or b.endswith("ed")):
        return False
    longer, shorter = (a, b) if a.endswith("ed") else (b, a)
    if len(longer) - len(shorter) not in (1, 2):
        return False
    return longer[:-1] == shorter or longer[:-2] == shorter


@_rule("n_ng_confusion", 0.5, "词尾 /n/ 与 /ŋ/ 混淆（sin → sing）")
def _n_ng(a: str, b: str) -> bool:
    return (
        (a.endswith("n") and b == a + "g")
        or (b.endswith("n") and a == b + "g")
        or (a.endswith("ng") and b == a[:-1])
        or (b.endswith("ng") and a == b[:-1])
    )


# Rules applied against phonetic keys rather than spelling.
_KEY_RULES: List[Tuple[str, float, str]] = [
    ("th_to_s", 1.0, "咬舌音 /θ/ 被读成 /s/（think → sink）"),
    ("th_to_f", 1.0, "咬舌音 /θ/ 被读成 /f/（three → free）"),
    ("v_to_w", 1.0, "/v/ 与 /w/ 混淆（very → wary）"),
    ("r_l_confusion", 0.5, "/r/ 与 /l/ 混淆"),
]


def detect_l1_error(target_word: str, spoken_word: str) -> Optional[Tuple[str, float, str]]:
    """Return (pattern_id, penalty_factor, label) if a known L1 pattern applies."""
    a = clean_word(target_word)
    b = clean_word(spoken_word)
    if not a or not b or a == b:
        return None
    # Only consider genuinely close spelling pairs; otherwise it is just a
    # different word and not a diagnosable accent pattern.
    if abs(len(a) - len(b)) > 3:
        return None
    # 1. Phonetic keys catch the pairs whose *spelling* differs too much for a
    #    character rule (very/wary is V vs W, not a letter swap).
    key_a, key_b = phonetic_key(a), phonetic_key(b)
    if key_a and key_b and key_a != key_b:
        for pid, severity, label in _KEY_RULES:
            if pid in ("th_to_s", "th_to_f"):
                if ("0" in key_a) != ("0" in key_b):
                    if (pid == "th_to_s" and "S" in key_b) or (pid == "th_to_f" and "F" in key_b):
                        return pid, severity, label
                    if (pid == "th_to_s" and "S" in key_a) or (pid == "th_to_f" and "F" in key_a):
                        return pid, severity, label
            elif pid == "v_to_w" and key_a.replace("V", "W") == key_b:
                return pid, severity, label
            elif pid == "r_l_confusion" and key_a.replace("R", "L") == key_b:
                return pid, severity, label

    # 2. Spelling rules catch the remainder and give their specific labels.
    for pid, severity, label in _PAIR_RULES:
        predicate = _PAIR_FUNCS[pid]
        if predicate(a, b):  # type: ignore[operator]
            return pid, severity, label
    return None


def words_phonetically_equal(target: str, spoken: str) -> bool:
    """True when two tokens are indistinguishable (or equivalent) phonetically.

    A diagnosed L1 error signature always wins over a key collision: ``sin``
    and ``sing`` are not interchangeable just because a coarse key happens to
    agree, and ``three``/``free`` must never be treated as the same word.
    """
    a = clean_word(target)
    b = clean_word(spoken)
    if not a or not b:
        return False
    if a == b:
        return True
    expansion = CONTRACTION_MAP.get(a)
    if expansion and expansion.replace(" ", "") == b:
        return True
    expansion = CONTRACTION_MAP.get(b)
    if expansion and expansion.replace(" ", "") == a:
        return True
    # Connected-speech reductions: "wanna" is a correct realisation of "want to".
    reduction = REDUCTION_MAP.get(a)
    if reduction and reduction.replace(" ", "") == b:
        return True
    reduction = REDUCTION_MAP.get(b)
    if reduction and reduction.replace(" ", "") == a:
        return True
    if detect_l1_error(a, b) is not None:
        return False
    ka, kb = phonetic_key(a), phonetic_key(b)
    if ka and kb and ka == kb:
        return True
    # Homophone set for the handful of high-frequency traps.
    homophones = {
        frozenset({"their", "there", "they're"}),
        frozenset({"to", "too", "two"}),
        frozenset({"your", "you're"}),
        frozenset({"its", "it's"}),
        frozenset({"right", "write", "rite"}),
        frozenset({"know", "no"}),
        frozenset({"hear", "here"}),
        frozenset({"sea", "see"}),
        frozenset({"weather", "whether"}),
        frozenset({"then", "than"}),
        frozenset({"by", "buy", "bye"}),
    }
    for group in homophones:
        if a in group and b in group:
            return True
    return False


def l1_error_profile(pairs: List[Tuple[str, str]]) -> List[Dict[str, object]]:
    """Aggregate detected L1 patterns over (target, spoken) word pairs."""
    counts: Dict[str, Dict[str, object]] = {}
    for target, spoken in pairs:
        hit = detect_l1_error(target, spoken)
        if not hit:
            continue
        pid, severity, label = hit
        entry = counts.setdefault(pid, {"pattern": pid, "label": label, "severity": severity, "count": 0, "examples": []})
        entry["count"] = int(entry["count"]) + 1  # type: ignore[arg-type]
        examples = entry["examples"]
        assert isinstance(examples, list)
        if len(examples) < 3:
            examples.append(f"{target} → {spoken}")
    return sorted(counts.values(), key=lambda e: (-float(e["severity"]), -int(e["count"])))  # type: ignore[arg-type]
