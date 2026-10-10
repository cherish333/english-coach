"""Pronunciation and dictation assessment.

Two graders live here, sharing one tokenizer:

``evaluate_pronunciation``
    What the learner *said* versus the target sentence, judged **phonetically**
    rather than by spelling. The previous implementation scored
    ``difflib.SequenceMatcher`` similarity of the ASR transcript, which meant
    "sink" for "think" scored 96/100 (66% string similarity) — the exact
    minimal-pair error a Chinese learner most needs flagged was being rewarded.
    Now each substituted word is compared by a phonetic key, known L1 transfer
    patterns are named explicitly, and the score reflects whether the target
    *phonemes* were produced.

``grade_dictation``
    What the learner *typed* versus the target, for recall/dictation practice
    where the answer is not visible. Reports omissions, substitutions and
    transpositions so the practice loop can schedule the right items.

Both are pure functions over text, so they need no audio model beyond the ASR
that already exists, and they are trivially unit-testable.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.phonetics import (
    CONTRACTION_MAP,
    EXPANDED_TO_CONTRACTION,
    PHRASE_REDUCTIONS,
    REDUCTION_MAP,
    clean_word,
    detect_l1_error,
    is_english_word,
    l1_error_profile,
    phonetic_key,
    words_phonetically_equal,
)

# Kept as a module-level alias for backwards compatibility.
_CONTRACTION_MAP = CONTRACTION_MAP

# Curated hints for words Chinese learners most often mispronounce. Only used
# when the learner actually got the word wrong, so it never wastes attention.
PHONETIC_HINTS = {
    "routine": "/ruːˈtiːn/ ⚠️ 重音在第二音节，ou发长音 /uː/",
    "vocabulary": "/vəˈkæbjələri/ ⚠️ 注意 a 发大口梅花音 /æ/，次重音清晰",
    "comfortable": "/ˈkʌmftəbl/ ⚠️ 注意 or 通常不发音，发三音节而非四音节",
    "schedule": "/ˈskedʒuːl/ 或 /ˈʃedjuːl/ ⚠️ 美式发 /sk/，英式发 /ʃ/",
    "development": "/dɪˈveləpmənt/ ⚠️ 重音在第二音节 vel，切勿重读 de",
    "environment": "/ɪnˈvaɪrənmənt/ ⚠️ 注意 n 弱读，ronment发轻",
    "specifically": "/spəˈsɪfɪkli/ ⚠️ 重音在 sif，尾音 cally 念 /kli/",
    "opportunity": "/ˌɒpəˈtjuːnəti/ ⚠️ 第二音节弱读，重音在 /tjuː/",
    "probably": "/ˈprɒbəbli/ ⚠️ 口语中中间 b 常常轻读或连为 /ˈprɒbli/",
    "actually": "/ˈæktʃuəli/ ⚠️ ct 发 /ktʃ/，自然连读",
    "thoroughly": "/ˈθʌrəli/ ⚠️ 咬舌音 /θ/，后半部分发轻音 /əli/",
    "colleague": "/ˈkɒliːɡ/ ⚠️ 重音在首音节，ue 不发音",
    "pronunciation": "/prəˌnʌnsiˈeɪʃn/ ⚠️ 注意是 nun 而不是 noun",
    "think": "/θɪŋk/ ⚠️ 舌尖轻触上齿，不要读成 sink",
    "three": "/θriː/ ⚠️ 咬舌后接 r，不要读成 free",
    "thing": "/θɪŋ/ ⚠️ 咬舌音 /θ/，不要读成 sing",
    "this": "/ðɪs/ ⚠️ 浊咬舌音 /ð/，不要读成 dis/zis",
    "very": "/ˈveri/ ⚠️ 上齿咬下唇的 /v/，不要读成 wary",
    "leave": "/liːv/ ⚠️ 长元音 /iː/，与 live /lɪv/ 区分",
    "live": "/lɪv/ ⚠️ 短元音 /ɪ/，与 leave /liːv/ 区分",
    "world": "/wɜːld/ ⚠️ 卷舌 + 词尾 /ld/，不要吞掉 d",
    "word": "/wɜːrd/ ⚠️ 词尾 /d/ 要发出来，不要读成 war",
    "work": "/wɜːrk/ ⚠️ 词尾 /k/ 要发出来，不要读成 were",
    "would": "/wʊd/ ⚠️ 词尾 /d/ 要发出来，不要读成 woo",
}

_WORD_RE = re.compile(r"[a-zA-Z0-9]+(?:['\u2019\-][a-zA-Z0-9]+)*")

# Longest phrase reduction we recognise, used to bound the span search.
_MAX_PHRASE_WORDS = max((len(phrase.split()) for phrase in PHRASE_REDUCTIONS), default=2)

# Weight of each per-word status in the final score.
_STATUS_WEIGHT = {
    "correct": 1.0,
    "imprecise": 0.6,
    "accent": 0.5,
    "missing": 0.0,
}


def tokenize(text: str) -> List[str]:
    """Split a sentence into cleaned word tokens."""
    return [clean_word(w) for w in _WORD_RE.findall(text or "") if clean_word(w)]


def raw_words(text: str) -> List[str]:
    """Split a sentence into the original (punctuated) word forms."""
    return [w for w in _WORD_RE.findall(text or "") if w]


def _empty_result(target_text: str, spoken_text: str, feedback: str) -> Dict[str, Any]:
    return {
        "score": 0,
        "level": "needs_work",
        "feedback": feedback,
        "target_text": target_text,
        "spoken_text": spoken_text,
        "words": [],
        "stats": {"total_words": 0, "correct": 0, "imprecise": 0, "accent": 0, "missing": 0},
        "error_patterns": [],
        "grade": 1,
    }


def evaluate_pronunciation(target_text: str, spoken_text: str) -> Dict[str, Any]:
    """Evaluate spoken pronunciation against a target sentence.

    Returns per-word status plus an aggregate ``error_patterns`` list naming the
    systematic L1 transfer errors detected (so the whiteboard can explain *why*
    a word was flagged instead of just colouring it).
    """
    if not target_text or not target_text.strip():
        return _empty_result("", spoken_text, "目标句子为空")

    target_raw = raw_words(target_text)
    target_tokens = [clean_word(w) for w in target_raw]
    spoken_tokens = tokenize(spoken_text)

    if not target_tokens:
        return _empty_result(target_text, spoken_text, "目标句子中没有可评测的英文单词")

    if not spoken_tokens:
        word_results = [
            {"word": raw, "status": "missing", "tip": PHONETIC_HINTS.get(tok)}
            for raw, tok in zip(target_raw, target_tokens)
        ]
        return {
            "score": 0,
            "level": "needs_work",
            "feedback": "未检测到清晰发音，请点击麦克风大声朗读。",
            "target_text": target_text,
            "spoken_text": spoken_text,
            "words": word_results,
            "stats": {
                "total_words": len(target_tokens),
                "correct": 0,
                "imprecise": 0,
                "accent": 0,
                "missing": len(target_tokens),
            },
            "error_patterns": [],
            "grade": 1,
        }

    word_results: List[Optional[Dict[str, Any]]] = [None] * len(target_tokens)
    error_pairs: List[Tuple[str, str]] = []

    matcher = difflib.SequenceMatcher(None, target_tokens, spoken_tokens)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx in range(i1, i2):
                if words_phonetically_equal(target_tokens[idx], spoken_tokens[idx - i1 + j1]):
                    word_results[idx] = {"word": target_raw[idx], "status": "correct", "tip": None}
                else:
                    # Same spelling, but the phonetic key disagrees: an L1
                    # substitution that the ASR happened to transcribe verbatim.
                    hit = detect_l1_error(target_tokens[idx], spoken_tokens[idx - i1 + j1])
                    if hit:
                        pid, _sev, label = hit
                        error_pairs.append((target_tokens[idx], spoken_tokens[idx - i1 + j1]))
                        word_results[idx] = {
                            "word": target_raw[idx],
                            "status": "missing",
                            "tip": f"{label}｜{PHONETIC_HINTS.get(target_tokens[idx], '')}".strip("｜"),
                        }
                    else:
                        word_results[idx] = {"word": target_raw[idx], "status": "correct", "tip": None}
            continue

        if tag == "insert":
            continue

        spoken_slice = spoken_tokens[j1:j2]
        for idx in range(i1, i2):
            if word_results[idx] is not None:
                continue
            tok = target_tokens[idx]
            raw = target_raw[idx]
            hint = PHONETIC_HINTS.get(tok)

            # 1. Contraction equivalents, then connected-speech reductions, then
            #    the reverse direction (learner used a reduction for full words).
            expanded = CONTRACTION_MAP.get(tok)
            if expanded and all(p in spoken_slice for p in expanded.split()):
                word_results[idx] = {"word": raw, "status": "correct", "tip": "缩写形式匹配"}
                continue
            reduced = REDUCTION_MAP.get(tok)
            if reduced and all(p in spoken_slice for p in reduced.split()):
                word_results[idx] = {"word": raw, "status": "correct", "tip": "连读弱读匹配"}
                continue
            # Whole-phrase reductions, longest first: "I have got to" is realized
            # as "I gotta" in natural speech, and the dropped auxiliary must not
            # be reported as a missing word.
            for span_len in (_MAX_PHRASE_WORDS, 4, 3, 2):
                if idx + span_len > len(target_tokens):
                    continue
                canonical = " ".join(target_tokens[idx:idx + span_len])
                reduction = PHRASE_REDUCTIONS.get(canonical)
                if not reduction:
                    continue
                if reduction not in spoken_slice and reduction.replace(" ", "") not in spoken_slice:
                    continue
                if not all(word_results[k] is None for k in range(idx, idx + span_len)):
                    continue
                for pair_idx in range(idx, idx + span_len):
                    word_results[pair_idx] = {
                        "word": target_raw[pair_idx],
                        "status": "correct",
                        "tip": "连读弱读匹配",
                    }
                break
            if word_results[idx] is not None:
                continue
            if idx + 1 < len(target_tokens):
                combo = f"{tok} {target_tokens[idx + 1]}"
                contraction = EXPANDED_TO_CONTRACTION.get(combo)
                if contraction and contraction in spoken_slice:
                    for pair_idx in (idx, idx + 1):
                        word_results[pair_idx] = {
                            "word": target_raw[pair_idx],
                            "status": "correct",
                            "tip": "缩写形式匹配",
                        }
                    continue

            # 2. Phonetic match against any spoken token in this slot.
            matched: Optional[str] = None
            for s_tok in spoken_slice:
                if words_phonetically_equal(tok, s_tok):
                    matched = s_tok
                    break
            if matched is not None:
                word_results[idx] = {"word": raw, "status": "correct", "tip": None}
                continue

            # 3. Diagnose a named L1 transfer error.
            best_hit: Optional[Tuple[str, float, str, str]] = None
            for s_tok in spoken_slice:
                hit = detect_l1_error(tok, s_tok)
                if hit:
                    pid, severity, label = hit
                    if best_hit is None or severity > best_hit[1]:
                        best_hit = (pid, severity, label, s_tok)

            if best_hit is not None:
                pid, severity, label, spoken_form = best_hit
                error_pairs.append((tok, spoken_form))
                status = "missing" if severity >= 1.0 else "accent"
                word_results[idx] = {
                    "word": raw,
                    "status": status,
                    "tip": f"{label}" + (f"｜{hint}" if hint else ""),
                }
                continue

            # 4. No diagnosable pattern: fall back to a spelling verdict so the
            #    learner still gets a graded outcome for unknown errors.
            best_sim, best_form = 0.0, ""
            for s_tok in spoken_slice:
                sim = difflib.SequenceMatcher(None, phonetic_key(tok), phonetic_key(s_tok)).ratio()
                if sim > best_sim:
                    best_sim, best_form = sim, s_tok
            if best_sim >= 0.75:
                word_results[idx] = {
                    "word": raw,
                    "status": "accent",
                    "tip": hint or f"听起来接近 '{best_form}'，注意重音与尾音",
                }
            elif best_sim >= 0.5:
                word_results[idx] = {
                    "word": raw,
                    "status": "imprecise",
                    "tip": hint or f"读作近似: '{best_form}'",
                }
            else:
                word_results[idx] = {
                    "word": raw,
                    "status": "missing",
                    "tip": hint or "发音未识别或脱落",
                }

    for idx in range(len(target_tokens)):
        if word_results[idx] is None:
            tok = target_tokens[idx]
            word_results[idx] = {
                "word": target_raw[idx],
                "status": "missing",
                "tip": PHONETIC_HINTS.get(tok),
            }

    resolved: List[Dict[str, Any]] = [w for w in word_results if w is not None]
    counts = {status: sum(1 for w in resolved if w["status"] == status) for status in _STATUS_WEIGHT}
    total = len(resolved)

    weighted = sum(_STATUS_WEIGHT[w["status"]] for w in resolved)
    score = int(round((weighted / max(total, 1)) * 100))
    # A meaning-changing error caps the score: the learner must not receive
    # "excellent" for a sentence containing a wrong phoneme.
    severe = sum(1 for w in resolved if w["status"] == "missing")
    if severe:
        score = min(score, 100 - 15 * severe)
    score = min(100, max(0, score))

    patterns = l1_error_profile(error_pairs)

    if score >= 90:
        level = "excellent"
        feedback = "🌟 发音非常地道！连读与语调节奏把握极佳！"
    elif score >= 75:
        level = "good"
        feedback = "👍 整体清晰，注意黄色标记单词的重音与清晰度。"
    elif score >= 60:
        level = "fair"
        feedback = "💪 句子结构完整，但有关键音素读错或吞音，建议对照原音再试一次。"
    else:
        level = "needs_work"
        feedback = "🎯 关键音素未读准，请先慢速跟读示范，重点纠正下方标注的音。"
    if patterns:
        feedback += " " + "；".join(str(p["label"]) for p in patterns[:2])

    if score >= 90:
        grade = 4
    elif score >= 75:
        grade = 3
    elif score >= 55:
        grade = 2
    else:
        grade = 1

    return {
        "score": score,
        "level": level,
        "feedback": feedback,
        "target_text": target_text,
        "spoken_text": spoken_text,
        "words": resolved,
        "stats": {
            "total_words": total,
            "correct": counts.get("correct", 0),
            "imprecise": counts.get("imprecise", 0),
            "accent": counts.get("accent", 0),
            "missing": counts.get("missing", 0),
        },
        "error_patterns": patterns,
        "grade": grade,
    }


def grade_dictation(target_text: str, typed_text: str) -> Dict[str, Any]:
    """Grade a recall/dictation attempt against the (hidden) target.

    Unlike pronunciation grading this is exact-ish text comparison: the learner
    typed what they remembered, so a wrong word is a memory error, not an accent.
    Returns word-level errors plus a 0-100 score and a 1-4 SRS rating.
    """
    target_tokens = tokenize(target_text)
    typed_tokens = tokenize(typed_text)
    if not target_tokens:
        return {
            "score": 0,
            "grade": 1,
            "level": "needs_work",
            "errors": [],
            "stats": {"total_words": 0, "correct": 0, "missed": 0, "wrong": 0, "extra": 0},
            "target_text": target_text,
            "typed_text": typed_text,
        }

    matcher = difflib.SequenceMatcher(None, target_tokens, typed_tokens)
    errors: List[Dict[str, Any]] = []
    correct = missed = wrong = extra = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            correct += i2 - i1
        elif tag == "delete":
            missed += i2 - i1
            for idx in range(i1, i2):
                errors.append({"type": "omission", "expected": target_tokens[idx], "got": ""})
        elif tag == "insert":
            extra += j2 - j1
            for idx in range(j1, j2):
                errors.append({"type": "extra", "expected": "", "got": typed_tokens[idx]})
        else:  # replace
            span = max(i2 - i1, j2 - j1)
            for offset in range(span):
                exp = target_tokens[i1 + offset] if i1 + offset < i2 else ""
                got = typed_tokens[j1 + offset] if j1 + offset < j2 else ""
                if exp and not got:
                    missed += 1
                    errors.append({"type": "omission", "expected": exp, "got": ""})
                elif got and not exp:
                    extra += 1
                    errors.append({"type": "extra", "expected": "", "got": got})
                else:
                    # Same letters, different order inside one word is a
                    # transposition; otherwise it is a substitution.
                    is_transposition = (
                        len(exp) == len(got)
                        and exp != got
                        and sorted(exp) == sorted(got)
                    )
                    if is_transposition:
                        errors.append({"type": "transposition", "expected": exp, "got": got})
                    else:
                        errors.append({"type": "substitution", "expected": exp, "got": got})
                    wrong += 1

    total = len(target_tokens)
    score = int(round((correct / max(total, 1)) * 100))
    if not typed_tokens:
        score = 0

    if score >= 95 and wrong == 0:
        grade = 4
        level = "excellent"
    elif score >= 80:
        grade = 3
        level = "good"
    elif score >= 50:
        grade = 2
        level = "fair"
    else:
        grade = 1
        level = "needs_work"

    return {
        "score": score,
        "grade": grade,
        "level": level,
        "errors": errors,
        "stats": {
            "total_words": total,
            "correct": correct,
            "missed": missed,
            "wrong": wrong,
            "extra": extra,
        },
        "target_text": target_text,
        "typed_text": typed_text,
    }


def mask_for_cloze(text: str, hide_ratio: float = 0.4, seed: int = 0) -> Tuple[str, List[str]]:
    """Blank a deterministic fraction of content words for cloze practice.

    Returns ``(masked_text, hidden_words)``. Long, less predictable words are
    chosen first so the exercise targets the informative content rather than
    "the"/"of".
    """
    words = raw_words(text)
    if not words:
        return text, []

    candidates = [
        (idx, w) for idx, w in enumerate(words)
        if len(w) >= 4 and is_english_word(w.lower())
    ]
    if not candidates:
        return text, []

    hide_count = max(1, int(round(len(candidates) * max(0.1, min(hide_ratio, 0.8)))))
    # Longest words first, tie-broken by position for determinism.
    ranked = sorted(candidates, key=lambda pair: (-len(pair[1]), pair[0]))
    chosen = sorted(idx for idx, _ in ranked[:hide_count])

    out: List[str] = []
    hidden: List[str] = []
    chosen_set = set(chosen)
    for idx, w in enumerate(words):
        if idx in chosen_set:
            hidden.append(w)
            out.append("_" * max(3, min(len(w), 12)))
        else:
            out.append(w)
    # Rebuild with the original separators preserved as spaces; the UI renders
    # its own layout, so exact whitespace does not matter here.
    return " ".join(out), hidden
