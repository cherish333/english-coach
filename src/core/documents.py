"""Small, local-only document library for the textbook lecture mode."""

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Optional

import pymupdf
from src.config import VOCABULARY_PDF, GRAMMAR_PDF, WESTERN_CIV_PDF, CUSTOM_BOOKS_DIR


@dataclass(frozen=True)
class DocumentSpec:
    document_id: str
    title: str
    path: Path
    description: str
    unit_count: int
    first_unit_page: int = 9
    is_custom: bool = False


DOCUMENT_SPECS = {
    "west_civ": DocumentSpec(
        document_id="west_civ",
        title="Western Civilization: A Brief History (7th Ed.)",
        path=WESTERN_CIV_PDF,
        description="Jackson J. Spielvogel 经典西方文明简史（原版）；30 个专题章节，涵盖古近东、希腊罗马、中世纪、文艺复兴至现代全球史。",
        unit_count=30,
        first_unit_page=38,
        is_custom=False,
    ),
    "vocabulary": DocumentSpec(
        document_id="vocabulary",
        title="English Vocabulary in Use — Pre-intermediate & Intermediate",
        path=VOCABULARY_PDF,
        description="Cambridge vocabulary textbook; 100 units, upper A2–B1.",
        unit_count=100,
        first_unit_page=9,
        is_custom=False,
    ),
    "grammar": DocumentSpec(
        document_id="grammar",
        title="English Grammar in Use — Fifth Edition",
        path=GRAMMAR_PDF,
        description="Grammar reference and practice textbook organized by unit.",
        unit_count=145,
        first_unit_page=9,
        is_custom=False,
    ),
}



class DocumentError(ValueError):
    """Raised for an unknown document or page."""


class DocumentLibrary:
    """Thread-safe, bounded cache around the two local textbooks."""

    def __init__(self, specs=None, image_cache_size: int = 8):
        self.specs = dict(specs or DOCUMENT_SPECS)
        self.image_cache_size = image_cache_size
        self._documents = {}
        self._image_cache = OrderedDict()
        self._lock = threading.RLock()
        self._load_custom_documents()

    def _load_custom_documents(self):
        CUSTOM_BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        catalog_file = CUSTOM_BOOKS_DIR / "catalog.json"
        catalog = {}
        if catalog_file.is_file():
            try:
                with open(catalog_file, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
            except Exception:
                catalog = {}

        for pdf_path in sorted(CUSTOM_BOOKS_DIR.glob("*.pdf")):
            clean_name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', pdf_path.stem)
            if not clean_name.strip('_'):
                clean_name = hashlib.md5(pdf_path.stem.encode('utf-8', errors='ignore')).hexdigest()[:8]
            doc_id = f"custom_{clean_name}"
            if doc_id in self.specs:
                doc_id = f"{doc_id}_{hashlib.md5(str(pdf_path).encode()).hexdigest()[:6]}"
            if doc_id in self.specs:
                continue
            entry = catalog.get(doc_id, {})
            title = entry.get("title")
            unit_count = entry.get("unit_count", 0)
            if not title or not unit_count:
                try:
                    doc = pymupdf.open(str(pdf_path))
                    title = title or doc.metadata.get("title") or pdf_path.stem
                    unit_count = unit_count or len(doc)
                    doc.close()
                except Exception:
                    title = title or pdf_path.stem
                    unit_count = unit_count or 1
            desc = entry.get("description", f"用户上传教材 ({pdf_path.name})")
            self.specs[doc_id] = DocumentSpec(
                document_id=doc_id,
                title=title.strip() or pdf_path.stem,
                path=pdf_path,
                description=desc,
                unit_count=unit_count,
                first_unit_page=1,
                is_custom=True,
            )

    def add_custom_document(self, filename: str, content: bytes, title: Optional[str] = None, description: Optional[str] = None) -> dict:
        CUSTOM_BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        clean_stem = re.sub(r'[^a-zA-Z0-9_\-\u4e00-\u9fa5]', '_', Path(filename).stem)
        if not clean_stem.strip('_-'):
            clean_stem = hashlib.md5(Path(filename).stem.encode('utf-8', errors='ignore')).hexdigest()[:8]
        safe_filename = f"{clean_stem}.pdf"
        target_path = CUSTOM_BOOKS_DIR / safe_filename
        
        counter = 1
        while target_path.exists():
            target_path = CUSTOM_BOOKS_DIR / f"{clean_stem}_{counter}.pdf"
            counter += 1

        target_path.write_bytes(content)

        try:
            doc = pymupdf.open(str(target_path))
            pages = len(doc)
            detected_title = doc.metadata.get("title") if doc.metadata else ""
            doc.close()
        except Exception as e:
            if target_path.exists():
                target_path.unlink()
            raise DocumentError(f"Uploaded file is not a valid PDF: {e}")

        final_title = (title or detected_title or Path(filename).stem).strip()
        final_desc = (description or f"用户自定义教材 ({pages} 页)").strip()
        clean_name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '_', target_path.stem)
        if not clean_name.strip('_'):
            clean_name = hashlib.md5(target_path.stem.encode('utf-8', errors='ignore')).hexdigest()[:8]
        doc_id = f"custom_{clean_name}"

        spec = DocumentSpec(
            document_id=doc_id,
            title=final_title,
            path=target_path,
            description=final_desc,
            unit_count=pages,
            first_unit_page=1,
            is_custom=True,
        )

        with self._lock:
            self.specs[doc_id] = spec
            catalog_file = CUSTOM_BOOKS_DIR / "catalog.json"
            catalog = {}
            if catalog_file.is_file():
                try:
                    with open(catalog_file, "r", encoding="utf-8") as f:
                        catalog = json.load(f)
                except Exception:
                    catalog = {}
            catalog[doc_id] = {
                "title": final_title,
                "filename": target_path.name,
                "description": final_desc,
                "unit_count": pages,
                "first_unit_page": 1,
            }
            try:
                with open(catalog_file, "w", encoding="utf-8") as f:
                    json.dump(catalog, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        return {
            "id": doc_id,
            "title": final_title,
            "description": final_desc,
            "pages": pages,
            "available": True,
            "is_custom": True,
        }

    def delete_custom_document(self, document_id: str) -> bool:
        spec = self._get_spec(document_id)
        if not spec.is_custom:
            raise DocumentError("Only custom documents can be deleted.")
        with self._lock:
            if document_id in self._documents:
                self._documents[document_id].close()
                del self._documents[document_id]
            if document_id in self.specs:
                del self.specs[document_id]
            # Evict cached rendered page images for this document
            cached_keys_to_del = [k for k in self._image_cache if k[0] == document_id]
            for k in cached_keys_to_del:
                self._image_cache.pop(k, None)
            if spec.path.exists():
                spec.path.unlink()
            catalog_file = CUSTOM_BOOKS_DIR / "catalog.json"
            if catalog_file.exists():
                try:
                    with open(catalog_file, "r", encoding="utf-8") as f:
                        catalog = json.load(f)
                    if document_id in catalog:
                        del catalog[document_id]
                        with open(catalog_file, "w", encoding="utf-8") as f:
                            json.dump(catalog, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
        return True

    def _get_spec(self, document_id: str) -> DocumentSpec:
        try:
            return self.specs[document_id]
        except KeyError as exc:
            raise DocumentError(f"Unknown document: {document_id}") from exc

    def _get_document(self, document_id: str):
        spec = self._get_spec(document_id)
        if not spec.path.is_file():
            raise DocumentError(f"Document file is missing: {spec.path}")
        with self._lock:
            document = self._documents.get(document_id)
            if document is None:
                document = pymupdf.open(str(spec.path))
                self._documents[document_id] = document
            return document

    def list_documents(self) -> list[dict]:
        result = []
        for spec in self.specs.values():
            pages = None
            available = spec.path.is_file()
            if available:
                try:
                    pages = len(self._get_document(spec.document_id))
                except Exception:
                    available = False
            result.append({
                "id": spec.document_id,
                "title": spec.title,
                "description": spec.description,
                "pages": pages,
                "available": available,
                "is_custom": spec.is_custom,
            })
        return result


    def get_page(self, document_id: str, page_number: int) -> dict:
        document = self._get_document(document_id)
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            raise DocumentError("Page number must be an integer")
        if page_number < 1 or page_number > len(document):
            raise DocumentError(
                f"Page {page_number} is out of range; document has {len(document)} pages"
            )

        sentences = self.get_page_sentences(document_id, page_number)
        if sentences:
            text = "\n".join(s["text"] for s in sentences)
        else:
            with self._lock:
                page = document[page_number - 1]
                text = page.get_text("text", sort=False).strip()
        return {
            "id": document_id,
            "title": self._get_spec(document_id).title,
            "page": page_number,
            "pages": len(document),
            "text": text,
            "has_text": bool(text),
        }

    def get_page_sentences(self, document_id: str, page_number: int) -> list[dict]:
        """Extract clean, structured sentences from the PDF page for sentence-by-sentence teaching."""
        document = self._get_document(document_id)
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            raise DocumentError("Page number must be an integer")
        if page_number < 1 or page_number > len(document):
            raise DocumentError(f"Page {page_number} out of range")

        COMMON_SHORT_WORDS = {
            "of", "in", "to", "at", "on", "by", "is", "as", "or", "he", "we", "it",
            "no", "an", "be", "so", "my", "up", "do", "if", "me", "us", "am", "go",
            "the", "and", "c."
        }

        def clean_text_artifacts(text: str) -> str:
            # Normalize spaced ellipses
            text = re.sub(r"(?:\.\s+){2,}\.?", "... ", text)
            text = re.sub(r"\.\s+\.\s+\.", "... ", text)
            # Trailing footnote numbers after closing quote or after word+punct
            text = re.sub(r"(?<=[”’\"'])\d{1,2}(?=\s|$)", "", text)
            text = re.sub(r"(?<=[a-zA-Z][.!?])\d{1,2}(?=\s|$)", "", text)
            # Ligature repair
            text = re.sub(r"\bTh\s+(e|is|at|ese|ose|ey|ere|eir|en|ird|irteenth|irty|ousand|rough|oughout)\b", r"Th\1", text, flags=re.IGNORECASE)
            text = re.sub(r"\bConfl\s+ict\b", "Conflict", text, flags=re.IGNORECASE)
            text = re.sub(r"\bEff\s+ects\b", "Effects", text, flags=re.IGNORECASE)
            text = re.sub(r"\bTh\s+e\b", "The", text)
            text = re.sub(r"\b(eff|diff|off|suff)\s+(ort|er|ice|icient|erence|erent)\b", r"\1\2", text, flags=re.IGNORECASE)
            text = re.sub(r"(\b[a-zA-Z]{2,})-\s+([a-z]{2,}\b)", r"\1\2", text)
            text = re.sub(r"[ \t]+", " ", text).strip()
            return text

        def is_noise_or_map_fragment(text: str) -> bool:
            t = text.strip()
            if not t:
                return True

            # 1. Clear vector font stutter (never in real English text)
            if re.search(r"([a-zA-Z]{2,4})\1{2,}", t, re.IGNORECASE):
                return True
            if re.search(r"(\b\w{2,}\b)(?:\s+\1){2,}", t, re.IGNORECASE):
                return True
            if re.search(r"(?:0,){2,}|(?:00\s+){2,}", t):
                return True

            # 2. Scale bars and degree coordinates (always short isolated tokens)
            words = t.split()
            if len(words) <= 8 and re.search(r"\b0\s+\d+.*(Miles?|Kilometers?|Kilom)\b", t, re.IGNORECASE):
                return True
            if len(words) <= 8 and (re.search(r"[\d\s][˚°]", t) or t.endswith(("˚", "°"))):
                return True

            # 3. Spaced single-letter vector drawings (e.g. "A n d e s M t s .")
            single_letters = sum(1 for w in words if len(w) == 1 and w.isalpha() and w.lower() not in ("a", "i"))
            if len(words) >= 3 and single_letters / len(words) > 0.35:
                return True
            if len(re.findall(r"[a-zA-Z0-9]", t)) < 2:
                return True

            # 4. Never drop captions, tables, figures, chronology
            if re.match(r"^(MAP|TABLE|FIGURE|CHRONOLOGY)\b", t, re.IGNORECASE):
                return False

            # 5. Never drop substantive sentences / paragraphs
            if len(words) >= 6 and (re.search(r"[.!?]", t) or re.search(r"[,;:]", t)):
                return False

            # 6. For short snippets (< 6 words): check for vector fragments or isolated labels
            clean_words = [w.lower().rstrip(".,;:()") for w in words]
            bad_short = sum(1 for w in clean_words if len(w) <= 2 and w.isalpha() and w not in COMMON_SHORT_WORDS and w not in ("i", "a", "iv", "vi", "ix", "xi"))
            if bad_short >= 2:
                return True

            # Short isolated map labels without punctuation or sentence verb
            if len(words) <= 4 and not re.search(r"[.!?:;,—–\(\)]", t):
                if not re.search(r"\b(is|are|was|were|has|have|had|flourished|emerged|became|began|lived|made|created|found)\b", t, re.IGNORECASE):
                    if not re.search(r"\b(Chapter|Unit|Part|Section|Outlines?|Questions?|Humans?|Civilization|Revolution|Cities|Standard|Study|Learning|Vocabulary|Grammar)\b", t, re.IGNORECASE):
                        return True
            if re.search(r"\b[A-Z]{3,}\s+[A-Z]\b", t) and len(words) <= 4:
                return True
            if re.search(r"\b(Pacific|Atlantic|Indian|Arctic)\s+(At\s+)?Ocean\b", t, re.IGNORECASE) and len(words) <= 4:
                return True
            return False

        with self._lock:
            page = document[page_number - 1]
            w, h = page.rect.width, page.rect.height
            mid = w / 2.0
            raw_blocks = page.get_text("blocks", sort=False)

            valid_blocks = []
            for b in raw_blocks:
                if b[6] != 0:
                    continue
                t = clean_text_artifacts(b[4].strip())
                if b[1] < 20 or b[3] > h - 20:
                    continue
                if t.isdigit() and (b[1] < 45 or b[3] > h - 45):
                    continue
                if re.search(r"\b\d+\s+C\s*H\s*A\s*P\s*T\s*E\s*R\b", t, re.IGNORECASE) and (b[1] < 45 or b[3] > h - 45):
                    continue
                if re.search(r"\b[A-Z\s]{4,}\s+\d+$", t) and len(t.split()) <= 5 and (b[1] < 45 or b[3] > h - 45):
                    continue
                if re.search(r"©|\bc\s+[A-Z][a-z]+.*CORBIS|Photo credit|Courtesy of|All rights reserved|Art Resource|Bridgeman", t, re.IGNORECASE):
                    continue
                if is_noise_or_map_fragment(t):
                    continue
                valid_blocks.append((b[0], b[1], b[2], b[3], t, b[5], b[6]))

            # Multi-column layout detection and column-aware ordering
            spanning_blocks = [b for b in valid_blocks if b[0] < mid - 20 and b[2] > mid + 20]
            spanning_ids = {id(b) for b in spanning_blocks}
            left_blocks = [b for b in valid_blocks if id(b) not in spanning_ids and b[2] <= mid + 35 and b[0] < mid - 20]
            right_blocks = [b for b in valid_blocks if id(b) not in spanning_ids and b[0] >= mid - 35 and b[2] > mid + 20]

            is_two_col = len(left_blocks) >= 2 and len(right_blocks) >= 2

            if is_two_col:
                # Gutter/center-seam boundary blocks that do not strictly meet criteria
                assigned_ids = {id(b) for b in (left_blocks + right_blocks + spanning_blocks)}
                unassigned_blocks = [b for b in valid_blocks if id(b) not in assigned_ids]
                for b in unassigned_blocks:
                    cx = (b[0] + b[2]) / 2
                    if b[0] < mid - 10 and b[2] > mid + 10:
                        spanning_blocks.append(b)
                    elif cx < mid:
                        left_blocks.append(b)
                    else:
                        right_blocks.append(b)

                right_narrative = []
                sidebars = []
                for b in right_blocks:
                    t = b[4].strip()
                    if re.match(r"^(CHRONOLOGY|TABLE|NOTE:)\b", t, re.IGNORECASE) or (sidebars and b[1] < 150):
                        sidebars.append(b)
                    else:
                        right_narrative.append(b)

                all_col = left_blocks + right_narrative
                y_col_start = min((b[1] for b in all_col), default=0)
                y_col_end = max((b[3] for b in all_col), default=h)

                top_span = sorted([b for b in spanning_blocks if b[3] <= y_col_start + 15], key=lambda b: b[1])
                bot_span = sorted([b for b in spanning_blocks if b[1] >= y_col_end - 15], key=lambda b: b[1])
                mid_span = sorted([b for b in spanning_blocks if b not in top_span and b not in bot_span], key=lambda b: b[1])

                left_sorted = sorted(left_blocks, key=lambda b: b[1])
                right_sorted = sorted(right_narrative, key=lambda b: b[1])
                sidebar_sorted = sorted(sidebars, key=lambda b: b[1])

                ordered_blocks = []
                seen_block_ids = set()

                def add_blocks(blocks):
                    for b in blocks:
                        if id(b) not in seen_block_ids:
                            seen_block_ids.add(id(b))
                            ordered_blocks.append(b)

                add_blocks(top_span)
                add_blocks(mid_span)
                add_blocks(left_sorted)
                add_blocks(right_sorted)
                add_blocks(sidebar_sorted)
                add_blocks(bot_span)

                # Ensure all valid blocks are preserved in vertical reading position
                leftover = sorted([b for b in valid_blocks if id(b) not in seen_block_ids], key=lambda b: b[1])
                if leftover:
                    add_blocks(leftover)
            else:
                ordered_blocks = sorted(valid_blocks, key=lambda b: b[1])

            # Dehyphenate lines and preserve standalone TOC / list items
            raw_texts = []
            for b in ordered_blocks:
                t = b[4].strip()
                lines = t.split("\n")
                curr = ""
                for line in lines:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    if curr:
                        if re.search(r"\s+\d{1,4}$", curr):
                            raw_texts.append(curr)
                            curr = line_s
                        elif curr.endswith("-") and line_s and line_s[0].islower():
                            curr = curr[:-1] + line_s
                        else:
                            curr += " " + line_s
                    else:
                        curr = line_s
                if curr:
                    raw_texts.append(curr)

            # Stitch cross-block continuations
            stitched_texts = []
            for t in raw_texts:
                if not stitched_texts:
                    stitched_texts.append(t)
                    continue

                prev = stitched_texts[-1]
                is_curr_header = bool(re.match(r"^(CHRONOLOGY|TABLE|MAP|FIGURE)\b", t, re.IGNORECASE))

                first_word = t.split()[0] if t.split() else ""
                last_word = prev.split()[-1].lower().rstrip(",-—–") if prev.split() else ""

                is_prev_dangling = prev.endswith((",", "-", "—", "–")) or last_word in ("and", "or", "in", "to", "of", "with", "by", "from", "as", "surrounding", "a", "an", "the", "their", "its")
                is_curr_lowercase = first_word[0].islower() if first_word else False

                should_stitch = not is_curr_header and (is_prev_dangling or is_curr_lowercase)

                if should_stitch:
                    if prev.endswith("-") and is_curr_lowercase:
                        stitched_texts[-1] = prev[:-1] + t
                    else:
                        stitched_texts[-1] = prev.rstrip("-") + " " + t
                else:
                    stitched_texts.append(t)



            abbr_patterns = [
                r"\b(e\.g|i\.e|etc|vs|vol|vols|approx|dept|est|fig|pp|ch|no|sec|cf|ed|eds)\.",
                r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Gov|Gen|Col)\.",
                r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.",
                r"\b(a\.m|p\.m)\.",
                r"\b(c|ca)\.\s*(?=\d)",
                r"\b(B\.C\.E|C\.E|B\.C|A\.D)\.",
                r"\b([A-Z]\.[A-Z]\.)",
                r"\b([A-Z]\.)\s+(?=[A-Z])",
                r"\b(\d+)\.(\d+)\b",
            ]

            def _is_inside_enclosures(s: str, pos: int) -> bool:
                prefix = s[:pos]
                if (prefix.count("(") - prefix.count(")")) > 0:
                    return True
                if (prefix.count("[") - prefix.count("]")) > 0:
                    return True
                if prefix.count('"') % 2 != 0:
                    return True
                if (prefix.count("“") - prefix.count("”")) > 0:
                    return True
                if (prefix.count("‘‘") - prefix.count("’’")) > 0:
                    return True
                return False

            def _is_independent_clause_conj(chunk: str, m: re.Match) -> bool:
                conj = m.group(1).lower()
                if conj in ("while", "whereas", "although", "however"):
                    return True
                after = chunk[m.end():].strip()
                words_after = after.split()
                if not words_after:
                    return False
                first_w = words_after[0].lower()
                subjects = {
                    "it", "its", "they", "them", "their", "he", "him", "his", "she", "her",
                    "we", "us", "our", "you", "your", "i", "my", "this", "that", "these", "those",
                    "the", "a", "an", "some", "many", "most", "all", "both", "each", "no",
                    "there", "one", "who", "what", "when", "where", "how", "why", "in", "by", "at", "on"
                }
                return first_w in subjects or words_after[0][0].isupper()

            def split_long_clause(chunk: str, max_words: int = 38, max_chars: int = 240) -> list[str]:
                words = chunk.split()
                if len(words) <= max_words and len(chunk) <= max_chars:
                    return [chunk]

                candidate_commas = [m for m in re.finditer(r",\s+", chunk) if not _is_inside_enclosures(chunk, m.start())]

                # If sentence has <= 2 candidate commas, keep intact up to 48 words
                # (Preserves quotes, appositives, relative clauses, and introductory phrases)
                if len(candidate_commas) <= 2 and len(words) <= 48:
                    return [chunk]

                # Check for coordinating conjunctions between independent clauses outside enclosures
                conj_pattern = re.compile(r",\s+(and|but|while|whereas|although|however|yet)\s+", re.IGNORECASE)
                valid_conjs = []
                for m in conj_pattern.finditer(chunk):
                    if not _is_inside_enclosures(chunk, m.start()) and _is_independent_clause_conj(chunk, m):
                        w1 = len(chunk[:m.start()].split())
                        w2 = len(chunk[m.start():].split())
                        if w1 >= 9 and w2 >= 9:
                            diff = abs(w1 - w2)
                            valid_conjs.append((diff, m))

                if valid_conjs:
                    valid_conjs.sort(key=lambda x: x[0])
                    best_m = valid_conjs[0][1]
                    s1 = best_m.start()
                    h1 = chunk[:s1].strip()
                    if not re.search(r"[.!?]$", h1):
                        h1 += "."
                    h2 = chunk[best_m.start() + 2:].strip()
                    if h2 and h2[0].islower():
                        h2 = h2[0].upper() + h2[1:]
                    return split_long_clause(h1, max_words, max_chars) + split_long_clause(h2, max_words, max_chars)

                # If no conjunction between independent clauses, keep intact up to 50 words
                if len(words) <= 50:
                    return [chunk]

                # Fallback only for massive blocks (> 50 words) lacking conjunctions
                if candidate_commas:
                    best_split = None
                    min_diff = 999
                    for m in candidate_commas:
                        w1 = len(chunk[:m.start()].split())
                        w2 = len(chunk[m.end():].split())
                        if w1 >= 10 and w2 >= 10:
                            diff = abs(w1 - w2)
                            if diff < min_diff:
                                min_diff = diff
                                best_split = (m.start(), m.end())

                    if best_split:
                        s1, e1 = best_split
                        h1 = chunk[:s1].strip()
                        if not re.search(r"[.!?]$", h1):
                            h1 += "."
                        h2 = chunk[e1:].strip()
                        if h2 and h2[0].islower():
                            h2 = h2[0].upper() + h2[1:]
                        return split_long_clause(h1, max_words, max_chars) + split_long_clause(h2, max_words, max_chars)

                return [chunk]

            final_sentences = []
            idx = 1

            def add_sentence(t: str):
                nonlocal idx
                t = t.strip()
                t = re.sub(r"^[•\-\*▪]\s*", "", t)
                t = re.sub(r"^\d+(?:\.\d+)*[.)]\s+", "", t).strip()
                t = re.sub(r"\s+", " ", t)
                if len(t) < 4 or not re.search(r"[a-zA-Z]", t):
                    return
                final_sentences.append({
                    "index": idx,
                    "text": t
                })
                idx += 1

            for seg in stitched_texts:
                seg = clean_text_artifacts(seg)
                bullet_chunks = re.split(r"\s*[•▪]\s*", seg)
                for b_chunk in bullet_chunks:
                    b_chunk = b_chunk.strip()
                    if not b_chunk:
                        continue

                    protected = b_chunk.replace("...", "§ELLIPSIS§")
                    for pat in abbr_patterns:
                        protected = re.sub(pat, lambda m: m.group(0).replace(".", "§DOT§"), protected, flags=re.IGNORECASE)

                    primary_chunks = re.split(
                        r'(?:(?<=[.!?。！？])|(?<=[.!?。！？]["\'”’）\)])|(?<=[.!?。！？]["\'”’）\)]{2}))\s+(?=[A-Z0-9"\'“‘\u4e00-\u9fff])',
                        protected
                    )

                    for chunk in primary_chunks:
                        chunk = chunk.replace("§DOT§", ".").replace("§ELLIPSIS§", "...").strip()
                        if not chunk:
                            continue

                        semi_chunks = [p.strip() for p in chunk.split(";") if p.strip()]
                        for s_chunk in semi_chunks:
                            if ":" in s_chunk and not re.search(r"\b(https?|ftp):", s_chunk):
                                colon_parts = s_chunk.split(":", 1)
                                if len(colon_parts[0].split()) >= 3 and len(colon_parts[1].split()) >= 3:
                                    colon_sub = [colon_parts[0].strip() + ":", colon_parts[1].strip()]
                                else:
                                    colon_sub = [s_chunk]
                            else:
                                colon_sub = [s_chunk]

                            for c_chunk in colon_sub:
                                sub_sentences = split_long_clause(c_chunk)
                                for final_s in sub_sentences:
                                    add_sentence(final_s)

            # Fallback if no sentences extracted but text exists
            if not final_sentences and page.get_text().strip():
                clean_full = re.sub(r"\s+", " ", page.get_text()).strip()
                if clean_full:
                    final_sentences.append({"index": 1, "text": clean_full})

            pw, ph = max(1.0, page.rect.width), max(1.0, page.rect.height)

            def extract_sentence_boxes(sentence_text: str) -> list[dict]:
                t = (sentence_text or "").strip()
                if not t:
                    return []
                clean = re.sub(r"^[•\-\*▪\d\.\)]\s*", "", t).strip()

                # 1. Exact or cleaned text search
                rects = page.search_for(t)
                if not rects and clean != t:
                    rects = page.search_for(clean)

                # 2. Subphrase / clause search
                if not rects:
                    parts = [p.strip() for p in re.split(r"[,;:—–]\s+", clean) if len(p.strip()) >= 10]
                    matched_parts = []
                    for p in parts:
                        r = page.search_for(p)
                        if r:
                            matched_parts.extend(r)
                    if matched_parts:
                        rects = matched_parts

                # 3. Sliding window of words (prefix or internal 3-word ngrams)
                if not rects:
                    words = clean.split()
                    if len(words) >= 3:
                        for k in range(min(len(words), 6), 2, -1):
                            r = page.search_for(" ".join(words[:k]))
                            if r:
                                rects = r
                                break
                if not rects:
                    words = clean.split()
                    for i in range(len(words) - 2):
                        r = page.search_for(" ".join(words[i:i + 3]))
                        if r:
                            rects = r
                            break

                if not rects:
                    return []

                # Deduplicate and sort rects in reading order (y, then x)
                unique = []
                for r in rects:
                    if not any(abs(r.x0 - u.x0) < 2 and abs(r.y0 - u.y0) < 2 and abs(r.x1 - u.x1) < 2 for u in unique):
                        unique.append(r)
                unique.sort(key=lambda r: (round(r.y0 / 6) * 6, r.x0))

                return [
                    {
                        "x": round((r.x0 / pw) * 100, 2),
                        "y": round((r.y0 / ph) * 100, 2),
                        "w": round((r.width / pw) * 100, 2),
                        "h": round((r.height / ph) * 100, 2),
                    }
                    for r in unique
                ]

            for s in final_sentences:
                s["boxes"] = extract_sentence_boxes(s.get("text", ""))

            return final_sentences

    def get_units(self, document_id: str) -> list[dict]:
        """Return unit or chapter list for standard textbooks and custom PDFs."""
        spec = self._get_spec(document_id)
        document = self._get_document(document_id)
        units = []

        with self._lock:
            # Special chapter extraction for Western Civilization
            if document_id == "west_civ":
                toc = document.get_toc()
                chap_idx = 1
                for item in toc:
                    lvl, title, page = item[0], item[1].strip(), item[2]
                    if lvl == 1 and re.match(r"^\d+\s+", title):
                        clean_title = re.sub(r"^\d+\s+", "", title).strip()
                        if clean_title.isupper():
                            clean_title = clean_title.title()
                        units.append({
                            "unit": chap_idx,
                            "page": page,
                            "title": f"Ch.{chap_idx} {clean_title} (p.{page})",
                        })
                        chap_idx += 1
                if units:
                    return units

            # For custom uploaded PDFs, extract TOC bookmarks first
            if spec.is_custom:
                toc = document.get_toc()
                if toc:
                    for idx, item in enumerate(toc, 1):
                        lvl, title, page = item[0], item[1].strip(), item[2]
                        if 1 <= page <= len(document):
                            units.append({
                                "unit": idx,
                                "page": page,
                                "title": title.strip() or f"Section {idx}",
                            })
                    if units:
                        return units

                # If no TOC in custom book: create clean chapter milestones (every 5-10 pages)
                total_p = len(document)
                step = 10 if total_p >= 40 else (5 if total_p >= 15 else 1)
                for idx, p in enumerate(range(1, total_p + 1, step), 1):
                    units.append({
                        "unit": idx,
                        "page": p,
                        "title": f"第 {p} 页 起" if step > 1 else f"第 {p} 页",
                    })
                return units

            # For standard Cambridge books
            for unit_number in range(1, spec.unit_count + 1):
                page_number = spec.first_unit_page + (unit_number - 1) * 2
                if page_number > len(document):
                    break
                text = document[page_number - 1].get_text("text", sort=True).strip()
                lines = [line.strip() for line in text.splitlines()[:3] if line.strip()]
                if not lines:
                    continue

                if document_id == "vocabulary":
                    heading = re.sub(r"^Study\s*", "", lines[0], flags=re.IGNORECASE)
                    heading = re.sub(rf"\s*unit\s*{unit_number}\s*$", "", heading, flags=re.IGNORECASE)
                    heading = re.sub(rf"{unit_number}\s*$", "", heading)
                    heading = re.sub(rf"^{unit_number}\s*", "", heading)
                elif document_id == "grammar":
                    heading_lines = lines[1:] if lines[0].lower() == "unit" else lines
                    heading = " ".join(heading_lines)
                    heading = re.sub(rf"^\s*{unit_number}\s*", "", heading)
                    heading = re.sub(rf"\s*{unit_number}\s*$", "", heading)
                else:
                    heading = f"Unit {unit_number}"

                heading = re.sub(r"\s+", " ", heading).strip(" -")
                units.append({
                    "unit": unit_number,
                    "page": page_number,
                    "title": heading or f"Unit {unit_number}",
                })

        return units


    def render_page(self, document_id: str, page_number: int) -> bytes:
        cache_key = (document_id, page_number)
        with self._lock:
            cached = self._image_cache.get(cache_key)
            if cached is not None:
                self._image_cache.move_to_end(cache_key)
                return cached

            document = self._get_document(document_id)
            if not isinstance(page_number, int) or isinstance(page_number, bool):
                raise DocumentError("Page number must be an integer")
            if page_number < 1 or page_number > len(document):
                raise DocumentError(
                    f"Page {page_number} is out of range; document has {len(document)} pages"
                )

            # A readable preview without creating permanent derived files.
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False)
            image = pixmap.tobytes("png")
            self._image_cache[cache_key] = image
            self._image_cache.move_to_end(cache_key)
            while len(self._image_cache) > self.image_cache_size:
                self._image_cache.popitem(last=False)
            return image

    def close(self):
        with self._lock:
            for document in self._documents.values():
                document.close()
            self._documents.clear()
            self._image_cache.clear()
