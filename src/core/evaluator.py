import re
import difflib
from typing import List, Dict, Any

# Common contraction equivalences
CONTRACTION_MAP = {
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

EXPANDED_TO_CONTRACTION = {v: k for k, v in CONTRACTION_MAP.items()}

# Phonetic tips for common tricky vocabulary
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
}

def clean_word(word: str) -> str:
    """Strip surrounding punctuation, normalize quotes, and lowercase."""
    word = word.replace('’', "'").replace('‘', "'").lower()
    return re.sub(r"^[^\w']+|[^\w']+$", '', word).strip(".,!?:;()")


def evaluate_pronunciation(target_text: str, spoken_text: str) -> Dict[str, Any]:
    """
    Evaluates spoken pronunciation against target sentence with word-level alignment.
    Returns:
      {
        "score": int (0-100),
        "level": str ("excellent" | "good" | "fair" | "needs_work"),
        "feedback": str,
        "target_text": str,
        "spoken_text": spoken_text,
        "words": [
          {"word": str, "status": "correct"|"imprecise"|"missing", "tip": str|None},
          ...
        ],
        "stats": {
          "total_words": int,
          "correct": int,
          "imprecise": int,
          "missing": int
        }
      }
    """
    if not target_text or not target_text.strip():
        return {
            "score": 0,
            "level": "needs_work",
            "feedback": "目标句子为空",
            "target_text": "",
            "spoken_text": spoken_text,
            "words": [],
            "stats": {"total_words": 0, "correct": 0, "imprecise": 0, "missing": 0}
        }

    raw_target_words = target_text.strip().split()
    target_tokens = [clean_word(w) for w in raw_target_words]

    raw_spoken_words = spoken_text.strip().split()
    spoken_tokens = [clean_word(w) for w in raw_spoken_words]

    # Handle empty spoken text
    if not spoken_tokens:
        word_results = []
        for raw, tok in zip(raw_target_words, target_tokens):
            tip = PHONETIC_HINTS.get(tok)
            word_results.append({
                "word": raw,
                "status": "missing",
                "tip": tip
            })
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
                "missing": len(target_tokens)
            }
        }

    # Match tokens using SequenceMatcher
    matcher = difflib.SequenceMatcher(None, target_tokens, spoken_tokens)
    word_results = [None] * len(target_tokens)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx in range(i1, i2):
                tok = target_tokens[idx]
                word_results[idx] = {
                    "word": raw_target_words[idx],
                    "status": "correct",
                    "tip": None
                }
        elif tag in ("replace", "delete"):
            spoken_slice = spoken_tokens[j1:j2]
            for idx in range(i1, i2):
                if word_results[idx] is not None:
                    continue

                tok = target_tokens[idx]
                tip = PHONETIC_HINTS.get(tok)
                
                # Check for contraction match (target contraction -> spoken expanded)
                matched_contraction = False
                expanded = CONTRACTION_MAP.get(tok)
                if expanded:
                    exp_parts = expanded.split()
                    if all(p in spoken_slice for p in exp_parts):
                        matched_contraction = True

                # Check reverse contraction match (target expanded pair -> spoken contraction)
                if not matched_contraction and idx + 1 < len(target_tokens):
                    next_tok = target_tokens[idx + 1]
                    combo = f"{tok} {next_tok}"
                    if combo in EXPANDED_TO_CONTRACTION:
                        contr = EXPANDED_TO_CONTRACTION[combo]
                        if contr in spoken_slice:
                            word_results[idx] = {
                                "word": raw_target_words[idx],
                                "status": "correct",
                                "tip": "缩写形式匹配"
                            }
                            word_results[idx + 1] = {
                                "word": raw_target_words[idx + 1],
                                "status": "correct",
                                "tip": "缩写形式匹配"
                            }
                            continue

                if matched_contraction:
                    word_results[idx] = {
                        "word": raw_target_words[idx],
                        "status": "correct",
                        "tip": "缩写形式匹配"
                    }
                    continue

                # Check for close similarity (similarity >= 0.65)
                best_sim = 0.0
                best_spoken_match = ""
                for s_tok in spoken_slice:
                    sim = difflib.SequenceMatcher(None, tok, s_tok).ratio()
                    if sim > best_sim:
                        best_sim = sim
                        best_spoken_match = s_tok

                if best_sim >= 0.65:
                    word_results[idx] = {
                        "word": raw_target_words[idx],
                        "status": "imprecise",
                        "tip": tip or f"读作近似: '{best_spoken_match}'"
                    }
                else:
                    word_results[idx] = {
                        "word": raw_target_words[idx],
                        "status": "missing",
                        "tip": tip or "发音未识别或脱落"
                    }

    # Fill any None
    for idx in range(len(target_tokens)):
        if word_results[idx] is None:
            tok = target_tokens[idx]
            word_results[idx] = {
                "word": raw_target_words[idx],
                "status": "missing",
                "tip": PHONETIC_HINTS.get(tok)
            }

    correct_count = sum(1 for w in word_results if w["status"] == "correct")
    imprecise_count = sum(1 for w in word_results if w["status"] == "imprecise")
    missing_count = sum(1 for w in word_results if w["status"] == "missing")
    total = len(word_results)

    # Score calculation
    score = int(round(((correct_count * 1.0 + imprecise_count * 0.6) / max(total, 1)) * 100))
    score = min(100, max(0, score))

    if score >= 90:
        level = "excellent"
        feedback = "🌟 发音非常地道！连读与语调节奏把握极佳！"
    elif score >= 75:
        level = "good"
        feedback = "👍 发音清晰，节奏良好！留意黄色标记单词的重音与清晰度。"
    elif score >= 60:
        level = "fair"
        feedback = "💪 整体句子结构完整，但部分词有吞音或误读，建议对照原音再试一次。"
    else:
        level = "needs_work"
        feedback = "🎯 关键单词未完全读准，请点击上方原音示范慢速跟读一遍。"

    return {
        "score": score,
        "level": level,
        "feedback": feedback,
        "target_text": target_text,
        "spoken_text": spoken_text,
        "words": word_results,
        "stats": {
            "total_words": total,
            "correct": correct_count,
            "imprecise": imprecise_count,
            "missing": missing_count
        }
    }
