"""PDF parsing (with OCR fallback for scanned pages), heading detection,
chunking, embedding, and writing into ChromaDB.

Heading detection carries the fixes for known-bugs #2, #3, #4 from the
rebuild spec:
  - headings are stored per-source so lookups can be scoped to specific
    documents instead of the whole corpus (bug #2 was a *lookup*-side bug,
    but storage has to preserve `source`/`page` for that to be possible);
  - multi-line merge cap is 5 lines, not 3 (bug #4);
  - a bold line with >=4 words ending in .!? is treated as ordinary prose
    and excluded from merging into a heading run, so it can no longer
    swallow-and-drop an adjacent real heading (bug #3).

Two later additions layered on top:
  - OCR gets grayscale + Otsu binarization + deskew preprocessing before
    Tesseract (`_preprocess_for_ocr`), plus per-page average word
    confidence captured and carried all the way into chunk metadata, so a
    low-confidence scanned page is a visible, distinct signal from
    ordinary LLM-fabrication risk rather than silently mixed into it.
  - chunk_page_text() is structure-aware: it prefers splitting on detect_
    headings() output (one PASAL-with-sub-clauses or one LAMPIRAN/SOP-
    section per top-level chunk) over blind character-count windows,
    because a blind sliding window was confirmed to cut a single clause in
    half and land the fact a question asks about in a chunk missing its
    own value (the "Ayat 11.1" incident). Clause/step-marker splitting and
    a final sliding window still exist as fallbacks for content with no
    clean heading structure.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pdfplumber
import requests

from app import config, database

try:
    import cv2
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image

    _OCR_AVAILABLE = True
except ImportError:  # pragma: no cover - OCR deps are optional on this laptop
    _OCR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Page / line extraction
# ---------------------------------------------------------------------------

@dataclass
class Line:
    text: str
    bold: bool
    is_first_line_of_page: bool
    line_no: int


@dataclass
class Page:
    page_num: int  # 1-indexed
    lines: list[Line] = field(default_factory=list)
    full_text: str = ""
    had_text_layer: bool = True
    ocr_confidence: Optional[float] = None  # None for native-text pages


def _line_is_bold(chars: list[dict]) -> bool:
    if not chars:
        return False
    bold_chars = sum(1 for c in chars if "bold" in c.get("fontname", "").lower())
    return bold_chars >= max(1, len(chars) // 2)


def _extract_page_lines(page: "pdfplumber.page.Page") -> list[Line]:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []
    chars = page.chars
    # group words into lines by rounded 'top' position
    lines_map: dict[int, list[dict]] = {}
    for w in words:
        key = round(w["top"])
        lines_map.setdefault(key, []).append(w)

    ordered_tops = sorted(lines_map.keys())
    lines: list[Line] = []
    for idx, top in enumerate(ordered_tops):
        line_words = sorted(lines_map[top], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line_words).strip()
        if not text:
            continue
        # gather chars roughly on this line to check boldness
        line_chars = [
            c
            for c in chars
            if abs(round(c["top"]) - top) <= 2
        ]
        bold = _line_is_bold(line_chars)
        lines.append(
            Line(text=text, bold=bold, is_first_line_of_page=(idx == 0), line_no=idx)
        )
    return lines


_OCR_LANG = "ind+eng"  # Indonesian first: the corpus is predominantly Indonesian legal/SOP text
_OCR_DPI = 200  # 150-200 is the accepted range; low DPI silently hurts recognition quality


def _preprocess_for_ocr(image: "Image.Image") -> "Image.Image":
    """Grayscale + Otsu binarization + deskew. Confirmed to help more than
    any single Tesseract config tweak, per the OCR-quality checklist."""
    gray = np.array(image.convert("L"))
    _, binarized = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    coords = np.column_stack(np.where(binarized < 255))
    if len(coords) > 20:
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        if abs(angle) > 0.1:
            h, w = binarized.shape
            matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            binarized = cv2.warpAffine(
                binarized, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
    return Image.fromarray(binarized)


def _reconstruct_text_from_ocr_data(data: dict) -> str:
    """Rebuilds line text from Tesseract's own word-level bounding boxes
    instead of trusting image_to_string()'s line merging, which happily
    joins two side-by-side table columns into one interleaved line --
    confirmed live: a two-column title-block table ("FUNGSI: ... NOMOR:
    ...") came out as one line mixing both columns' fields and values,
    which a small LLM then couldn't reliably untangle. An unusually large
    horizontal gap between consecutive words Tesseract placed on the same
    detected line is a real table-cell boundary, not a longer-than-usual
    word space -- splitting there into a separate output line lets each
    column be read (and later heading/field-detection logic) on its own,
    without merging unrelated columns. Structural, from real pixel
    positions -- not a guess based on this corpus's specific field names."""
    n = len(data.get("text", []))
    groups: dict[tuple, list[int]] = {}
    for i in range(n):
        if data["level"][i] != 5 or not data["text"][i].strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(i)

    output_lines = []
    for key in sorted(groups.keys()):
        idxs = sorted(groups[key], key=lambda i: data["left"][i])
        words = [(data["left"][i], data["width"][i], data["text"][i]) for i in idxs]
        gaps = [words[k + 1][0] - (words[k][0] + words[k][1]) for k in range(len(words) - 1)]
        median_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
        gap_threshold = max(40, median_gap * 4)

        current = [words[0][2]]
        for k in range(1, len(words)):
            gap = words[k][0] - (words[k - 1][0] + words[k - 1][1])
            if gap > gap_threshold:
                output_lines.append(" ".join(current))
                current = [words[k][2]]
            else:
                current.append(words[k][2])
        output_lines.append(" ".join(current))

    return "\n".join(output_lines)


def _correct_orientation(image: "Image.Image") -> "Image.Image":
    """The main OCR pass assumes upright, left-to-right text -- a page whose
    real content is rotated 90/180/270 degrees (confirmed live: a real
    equipment-layout diagram laid out in landscape on a portrait page came
    out as near-total gibberish) gets read wrong from the start otherwise.
    Tesseract's Orientation and Script Detection (OSD) is a separate,
    cheaper pass that estimates the rotation before the real recognition
    attempt runs, so the image gets corrected instead of just misread.

    Confirmed live this can also make things WORSE: an already-upright,
    perfectly ordinary cover page got OSD's rotate=180 applied anyway,
    flipping it into gibberish (a correct page's OCR confidence dropped
    from the 80s/90s typical of this template to 40.0, extracted text
    became reversed/mirrored) -- because OSD's own confidence for that
    guess was only 0.53, essentially a coin flip, and it also misidentified
    the script as "Greek" (also low-confidence noise). The genuinely
    correct rotation case (the real rotated diagram) had orientation_conf
    4.71 -- a wide, clean gap from 0.53. Only trust OSD's rotation when
    it's actually confident; otherwise leave the page alone rather than
    risk turning a working page into garbage."""
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        rotation = osd.get("rotate", 0)
        confidence = osd.get("orientation_conf", 0)
    except Exception:
        # OSD can fail outright on near-blank/low-content pages -- treat as
        # "no rotation detected" rather than failing OCR for the whole page
        # over a failed pre-check.
        return image
    if not rotation or confidence < config.OCR_ORIENTATION_MIN_CONFIDENCE:
        return image
    return image.rotate(-rotation, expand=True)


def _ocr_page(pdf_path: Path, page_num: int) -> tuple[str, Optional[float]]:
    """Returns (text, average_word_confidence). Confidence is a distinct,
    visible signal from LLM-fabrication risk -- surfaced via answer logging
    rather than silently folded into ordinary retrieval/generation."""
    if not _OCR_AVAILABLE:
        return "", None
    try:
        images = convert_from_path(
            str(pdf_path), first_page=page_num, last_page=page_num, dpi=_OCR_DPI
        )
        if not images:
            return "", None
        oriented = _correct_orientation(images[0])
        processed = _preprocess_for_ocr(oriented)

        data = pytesseract.image_to_data(processed, lang=_OCR_LANG, output_type=pytesseract.Output.DICT)
        text = _reconstruct_text_from_ocr_data(data)
        confidences = [float(c) for c in data.get("conf", []) if c not in ("-1", -1) and float(c) >= 0]
        avg_confidence = round(sum(confidences) / len(confidences), 1) if confidences else None

        return text, avg_confidence
    except Exception:
        # OCR backends (poppler/tesseract) may not be installed on this
        # laptop; degrade gracefully rather than failing ingestion.
        return "", None


def _page_is_graphical(page: "pdfplumber.page.Page") -> bool:
    """A page whose real content is a diagram reads very differently at the
    structural level than either a flat scan or an ordinary text/table page
    -- confirmed live (see config.VLM_MIN_CURVES): a scan is one raster
    image with zero vector drawing primitives, an ordinary table-heavy page
    has plenty of rects (cell borders) but few curves, while a real
    vector-drawn diagram (pipes, circular pump/valve icons) is dense with
    curves. Curve count is therefore the discriminating signal -- image
    presence/area alone would wrongly flag every scanned page too."""
    return len(page.curves) >= config.VLM_MIN_CURVES


def _describe_page_image(pdf_path: Path, page_num: int) -> Optional[str]:
    """Renders the page and asks the vision-language model to describe it --
    only called for pages _page_is_graphical() flagged, never for ordinary
    pages. No grayscale/binarization here (unlike _ocr_page): a VLM benefits
    from color (e.g. distinguishing a red pipe from a blue one), where OCR
    only cares about character edges."""
    if not _OCR_AVAILABLE or not config.VLM_ENABLED:
        return None
    try:
        images = convert_from_path(
            str(pdf_path), first_page=page_num, last_page=page_num, dpi=_OCR_DPI
        )
        if not images:
            return None
        oriented = _correct_orientation(images[0])
    except Exception:
        return None
    return _describe_image_with_vlm(oriented)


def _describe_image_with_vlm(image: "Image.Image", _retried: bool = False) -> Optional[str]:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    try:
        resp = requests.post(
            f"{config.OLLAMA_HOST}/api/generate",
            json={
                "model": config.VLM_MODEL,
                "prompt": (
                    "Describe this diagram or image in detail. List every "
                    "visible label or text exactly as written, then describe "
                    "the shapes and how they connect (arrows, lines, flow "
                    "direction, groupings). Respond in the same language as "
                    "the labels in the image."
                ),
                "images": [b64],
                "stream": False,
                # Confirmed live: Ollama's default num_ctx (4096) for this
                # model left almost no room for a real answer -- the
                # rendered image alone consumed 4054 of those 4096 tokens,
                # so the response got cut off after one sentence
                # (done_reason "length", not an error). Raising it to 8192
                # let the same call finish naturally (done_reason "stop")
                # with a full multi-paragraph description.
                "options": {"num_ctx": config.VLM_CONTEXT_TOKENS},
            },
            timeout=config.VLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        # Same transient-reload behavior as generation.call_ollama() -- the
        # model may need to be swapped into VRAM (it can't stay resident
        # alongside OLLAMA_MODEL), and an occasional first request 500s
        # while that happens.
        if _retried:
            return None
        return _describe_image_with_vlm(image, _retried=True)
    except Exception:
        return None
    return resp.json().get("response", "").strip() or None


def _looks_like_real_text(text: str) -> bool:
    """A PDF's own text layer can be present but garbage -- confirmed live:
    a real equipment-layout diagram (drawn with a custom/broken font whose
    character codes don't map to real glyphs) came back from pdfplumber as
    a non-empty string of pure \\x01 control-character bytes. bool(text.
    strip()) alone treats that as a valid text layer and skips the OCR
    fallback entirely, losing even the diagram's visible labels -- worse
    than if the page had no text layer at all. A real text layer is
    overwhelmingly printable characters; a broken one is dominated by
    control/non-printable bytes, so that ratio is the structural signal
    (not particular characters or words) used to tell them apart."""
    stripped = text.strip()
    if not stripped:
        return False
    printable = sum(1 for ch in stripped if ch.isprintable() or ch in "\n\t")
    return printable / len(stripped) > 0.9


def extract_pdf_pages(pdf_path: Path) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            lines = _extract_page_lines(page)
            text = "\n".join(l.text for l in lines)
            had_text_layer = _looks_like_real_text(text)
            ocr_confidence = None
            if not had_text_layer:
                ocr_text, ocr_confidence = _ocr_page(pdf_path, i)
                text = ocr_text
                lines = [
                    Line(text=t, bold=False, is_first_line_of_page=(j == 0), line_no=j)
                    for j, t in enumerate(t for t in ocr_text.splitlines() if t.strip())
                ]

            if _page_is_graphical(page):
                vlm_description = _describe_page_image(pdf_path, i)
                if vlm_description:
                    label = "[Deskripsi diagram/gambar dari halaman ini]"
                    text = f"{text}\n\n{label}\n{vlm_description}" if text.strip() else f"{label}\n{vlm_description}"
                    lines = lines + [
                        Line(text=t, bold=False, is_first_line_of_page=False, line_no=len(lines) + j)
                        for j, t in enumerate(
                            l for l in (label + "\n" + vlm_description).splitlines() if l.strip()
                        )
                    ]

            pages.append(
                Page(
                    page_num=i, lines=lines, full_text=text,
                    had_text_layer=had_text_layer, ocr_confidence=ocr_confidence,
                )
            )
    return pages


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

_HEADING_PATTERNS = [
    re.compile(r"^(PASAL|Pasal)\s+\d+", re.IGNORECASE),
    re.compile(r"^(LAMPIRAN|Lampiran)\s+[A-Za-z0-9]+", re.IGNORECASE),
    re.compile(r"^(BAB|Bab)\s+[IVXLC0-9]+", re.IGNORECASE),
    # numbered section e.g. "3." or "3.1 Ruang Lingkup" -- the lookahead
    # requires a real letter somewhere in the trailing token, not just
    # \S+ (any non-space run). Confirmed live: without it, this matched a
    # revision-checkbox row ("REVISI KE : 0 1 2 3 4" renders as a bare
    # "0 1 2 3 4" text line) as a false "heading", which then split a
    # title-block metadata table in half at chunking time -- scattering a
    # document's own NOMOR/FUNGSI fields into one chunk and its BERLAKU
    # TMT/JUDUL/HALAMAN fields into another that doesn't repeat the
    # number, so a later lookup for either field could land on only one
    # half. A genuine numbered heading's text is always real words, never
    # just more bare digits, so this excludes checkbox/pagination-style
    # digit runs structurally instead of naming any specific corpus's
    # checkbox convention.
    re.compile(r"^\d+(\.\d+)*\.?\s+(?=\S*[A-Za-z])\S+"),
]

_SENTENCE_END = re.compile(r"[.!?]$")

# A real heading starts its own line/block. An in-body cross-reference like
# "...sebagaimana dimaksud dalam Pasal 15, dan disimpan..." only ever
# produces a false PASAL-pattern match when a PDF's line-wrap happens to
# fall right before "Pasal 15" -- the two structural tells of that case
# (not of a genuine heading) are: (a) the text right after the identifier
# keeps running as the same sentence (starts with a comma or a lowercase
# word), or (b) the text right before this line was itself still mid-
# sentence (no sentence-ending punctuation, last word lowercase), meaning
# this "heading" line is a wrapped continuation, not a fresh block start.
# Same reasoning family as the existing bold-ordinary-sentence exclusion
# (_is_ordinary_sentence) below -- structural position/continuation, not a
# hardcoded word list.
_MIDSENTENCE_CONTINUATION_RE = re.compile(r"^[,;:]?\s*[a-z]")

# Mirror-image case of the one above: an ordinary body paragraph whose
# first wrapped line simply happens to land at the top of a fresh page
# (arbitrary pagination, unrelated to any section boundary) reads exactly
# like a short heading -- properly capitalized, short first line, "first
# line of the page" -- but its content keeps running as the same sentence
# on the line right after it, ending only well past where a real heading
# would ("Sub-bagian 3 pada bagian Tujuan ... menjelaskan pemeriksaan" /
# "berkala terhadap peralatan ..." split exactly at the page break).  A
# genuine heading is never followed by a lowercase continuation of its own
# sentence -- the line after it always starts a new clause/sentence
# instead. Same family of check as _is_midsentence_reference above (a
# sentence continuing across a line boundary means that boundary isn't a
# real heading edge), just looking forward instead of backward.
_LOWERCASE_START_RE = re.compile(r"^[a-z]")

# "Halaman 3 dari 36" / "Page 3 of 36" / "Hal. 3/36" -- a page-number
# footer/header, a universal pagination convention (not a corpus-specific
# word list the way document-type or section-keyword vocabulary would be,
# same justification as the value-unit filter elsewhere in this codebase).
# Repetition-based filtering (_filter_repeating_headers below) can't
# reliably catch this on its own: it only repeats as a *captured heading
# candidate* when it happens to coincide with another candidacy signal
# (bold/first-of-page/pattern-match) on that particular page, so on a
# real scanned document it was captured only 2 times across 36 pages --
# nowhere near enough for a frequency threshold, even a low one, without
# risking false positives on genuinely-repeated short real headings.
# Matching the "<number> dari/of <number>" shape directly, anchored at
# the end of the line to tolerate whatever OCR-garbled prefix precedes it
# (confirmed live: "HALAMAN 1 12 dari 36", a misread colon), catches it
# regardless of how many times it was captured.
_PAGINATION_LINE_RE = re.compile(r"\d+\s*(?:dari|of|/)\s*\d+\s*$", re.IGNORECASE)


def _is_midsentence_reference(line_text: str, match_end: int, prev_line: Optional[Line]) -> bool:
    remainder = line_text[match_end:]
    if remainder.strip() and _MIDSENTENCE_CONTINUATION_RE.match(remainder):
        return True
    if prev_line is not None:
        prev_text = prev_line.text.strip()
        prev_words = prev_text.split()
        if prev_words and not _SENTENCE_END.search(prev_text) and prev_words[-1][0].islower():
            return True
    return False


def _looks_like_heading_candidate(line: Line, prev_line: Optional[Line] = None) -> bool:
    """A line is a heading candidate if it's bold OR the first line of the
    page OR matches a known structural pattern -- NOT merely "any short
    line", which floods results with false positives on documents full of
    short labeled fields (see known limitation notes)."""
    pattern_match = next((p.match(line.text) for p in _HEADING_PATTERNS if p.match(line.text)), None)
    if pattern_match:
        return not _is_midsentence_reference(line.text, pattern_match.end(), prev_line)
    # bold/first-of-page candidacy has no keyword pattern to anchor on, so a
    # line starting with a lowercase word (a stray cross-reference sentence
    # like "dalam Pasal 15, dan disimpan...", not a fragment mid-merge but a
    # complete sentence in its own right) is excluded outright -- every real
    # heading in this corpus starts with a capital letter or a digit.
    if _LOWERCASE_START_RE.match(line.text.strip()):
        return False
    if _PAGINATION_LINE_RE.search(line.text.strip()):
        return False
    if line.bold and len(line.text.split()) <= 12:
        return True
    if line.is_first_line_of_page and len(line.text.split()) <= 12:
        return True
    return False


def _is_ordinary_sentence(line: Line) -> bool:
    """Bug #3: a bold ordinary-prose sentence must not merge into (and
    thereby be able to silently delete) an adjacent heading."""
    words = line.text.split()
    return len(words) >= config.HEADING_MERGE_EXCLUDE_MIN_WORDS and bool(
        _SENTENCE_END.search(line.text.strip())
    )


def _filter_repeating_headers(headings: list[dict], total_pages: int) -> list[dict]:
    """A genuine heading occurs once; a running header/footer template (a
    page-number line, a revision-row, a document title repeated on every
    page) occurs -- verbatim except for the digits -- on a large fraction
    of the document's pages. Detecting that by REPETITION, not by matching
    a specific phrase (this corpus's "HALAMAN X dari Y" wouldn't
    generalize to a differently-worded or differently-languaged real
    document), filters running headers/footers out of the heading index
    generically -- this is the "running-header style heading
    detect_headings() missed" limitation flagged elsewhere in this module,
    now actually handled instead of just documented.

    A first-line-of-page heading immediately followed (same page) by one
    of these repeating lines is very likely OCR-garbled decorative
    header/margin text glued to that same running-header block (confirmed
    live: a scanned page's OCR misread its rotated/decorative margin text
    as "Lena OE ENE", immediately followed by "HALAMAN 21 dari 36") --
    even though the garbled text itself never repeats verbatim (OCR
    misreads it slightly differently each time), so it wouldn't be caught
    by the repetition check on its own text alone.
    """
    if total_pages < 4 or not headings:
        return headings

    def _normalize(text: str) -> str:
        return re.sub(r"\d+", "#", text.strip().lower())

    counts: dict[str, int] = {}
    for h in headings:
        norm = _normalize(h["text"])
        counts[norm] = counts.get(norm, 0) + 1

    repeat_threshold = max(3, total_pages // 3)
    repeating_norms = {norm for norm, c in counts.items() if c >= repeat_threshold}

    dropped_lines_by_page: dict[int, set[int]] = {}
    kept: list[dict] = []
    for h in headings:
        if _normalize(h["text"]) in repeating_norms:
            dropped_lines_by_page.setdefault(h["page"], set()).add(h["line_no"])
        else:
            kept.append(h)

    final: list[dict] = []
    for h in kept:
        page_drops = dropped_lines_by_page.get(h["page"], set())
        glued_to_dropped_header = h["line_no"] == 0 and any(ln <= 2 for ln in page_drops)
        if not glued_to_dropped_header:
            final.append(h)
    return final


def detect_headings(pages: list[Page]) -> list[dict]:
    """Returns a list of {page, text, line_no} heading dicts for one document."""
    headings: list[dict] = []

    for page_idx, page in enumerate(pages):
        i = 0
        n = len(page.lines)
        while i < n:
            line = page.lines[i]
            if i > 0:
                prev_line = page.lines[i - 1]
            else:
                # cross-page continuity: a candidate that's the first line
                # of its page needs the PREVIOUS page's last line to know
                # whether it's a wrapped mid-sentence continuation, not None
                # (which would always look like a fresh start).
                prev_line = None
                for back in range(page_idx - 1, -1, -1):
                    if pages[back].lines:
                        prev_line = pages[back].lines[-1]
                        break
            if not _looks_like_heading_candidate(line, prev_line):
                i += 1
                continue

            run_lines = [line]
            j = i + 1
            while (
                j < n
                and len(run_lines) < config.HEADING_MAX_MERGE_LINES
                and _looks_like_heading_candidate(page.lines[j], page.lines[j - 1])
                and not _is_ordinary_sentence(page.lines[j])
            ):
                run_lines.append(page.lines[j])
                j += 1

            # the line immediately after the merged run: cross into the
            # next page if the run ran off the end of this one.
            if j < n:
                next_line = page.lines[j]
            else:
                next_line = None
                for fwd in range(page_idx + 1, len(pages)):
                    if pages[fwd].lines:
                        next_line = pages[fwd].lines[0]
                        break
            # A candidate immediately followed by a lowercase continuation
            # of its own sentence, OR by a page-number footer/header line,
            # is page-top furniture (decorative margin text, a logo/stamp
            # OCR misread, a running header) glued to that footer -- not a
            # real heading -- even when the candidate's own text doesn't
            # itself match the pagination pattern (it's the OCR-garbled
            # neighbor of one).
            next_text = next_line.text.strip() if next_line is not None else ""
            prose_continuation = bool(next_text) and (
                _LOWERCASE_START_RE.match(next_text) or _PAGINATION_LINE_RE.search(next_text)
            )

            if len(run_lines) <= config.HEADING_MAX_MERGE_LINES and not prose_continuation:
                merged_text = " ".join(l.text for l in run_lines).strip()
                if merged_text:
                    headings.append(
                        {"page": page.page_num, "text": merged_text, "line_no": line.line_no}
                    )
            # else: run exceeded the cap, or reads straight into a lowercase
            # continuation of its own sentence -- drop it rather than store
            # something misleading (documented open limitation for the cap
            # case; a genuine body-paragraph false-positive for the other)
            i = j if j > i else i + 1

    return _filter_repeating_headers(headings, len(pages))


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

# A blind sliding-window split can cut a single clause/step in half, landing
# the fact a question asks about in a chunk that's missing its own value
# (confirmed on the longer regenerated corpus: "Ayat 11.1" split so the
# retrieved chunk had the clause number but not its "34 hari kerja" value).
# Split on structural boundaries first -- PASAL/LAMPIRAN/numbered-section
# headings, then sub-clause markers ("11.1" on its own line, or "3) ..."
# numbered step lines) -- so each clause/step stays intact in one chunk,
# and only fall back to a sliding window for a segment that's still too big.
_CLAUSE_MARKER_RE = re.compile(r"^\d+\.\d+$")
_STEP_MARKER_RE = re.compile(r"^\d+\)\s")


def _split_on_markers(lines: list[str], is_marker) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    for line in lines:
        if current and is_marker(line.strip()):
            segments.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        segments.append("\n".join(current).strip())
    return [s for s in segments if s]


def _sliding_window(text: str, size: int, overlap: int) -> list[str]:
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        start = end - overlap
    return pieces


def _find_heading_positions(text: str, heading_texts: list[str]) -> list[int]:
    """detect_headings() merges lines with a single space, while the source
    text keeps real newlines between them -- match whitespace-tolerantly to
    find each heading's actual start offset in the page text."""
    positions = []
    for heading in heading_texts:
        pattern = re.escape(heading).replace(r"\ ", r"\s+")
        m = re.search(pattern, text)
        if m:
            positions.append(m.start())
    return sorted(set(positions))


def _split_by_heading_positions(text: str, positions: list[int]) -> list[str]:
    if not positions:
        return [text] if text.strip() else []
    bounds = [0] + positions + [len(text)]
    bounds = sorted(set(bounds))
    segments = [text[bounds[i]:bounds[i + 1]].strip() for i in range(len(bounds) - 1)]
    return [s for s in segments if s]


def chunk_page_text(text: str, page_num: int, page_headings: Optional[list[str]] = None) -> list[dict]:
    """Structure-aware: a chunk boundary aligns with a detected heading
    (one PASAL-with-its-sub-clauses, or one LAMPIRAN/SOP-section, becomes
    one top-level segment) instead of an arbitrary character count. Falls
    back to the local heading-pattern regex when no headings were detected
    for this page (e.g. a running-header style heading detect_headings()
    missed -- see the known heading-detection limitation). Either way, an
    oversized segment still gets split further by clause/step markers, then
    a sliding window, so nothing breaks for content without clean structure."""
    text = text.strip()
    if not text:
        return []
    size = config.CHUNK_SIZE_CHARS
    overlap = config.CHUNK_OVERLAP_CHARS

    top_sections = None
    if page_headings:
        positions = _find_heading_positions(text, page_headings)
        if positions:
            top_sections = _split_by_heading_positions(text, positions)

    if top_sections is None:
        top_sections = _split_on_markers(
            text.split("\n"), lambda l: any(p.match(l) for p in _HEADING_PATTERNS)
        )

    final_texts: list[str] = []
    for section in top_sections:
        if len(section) <= size:
            final_texts.append(section)
            continue
        sub_segments = _split_on_markers(
            section.split("\n"),
            lambda l: bool(_CLAUSE_MARKER_RE.match(l) or _STEP_MARKER_RE.match(l)),
        )
        for sub in sub_segments:
            if len(sub) <= size:
                final_texts.append(sub)
            else:
                final_texts.extend(_sliding_window(sub, size, overlap))

    return [
        {"text": t, "page": page_num, "chunk_index": i}
        for i, t in enumerate(final_texts)
    ]


def content_hash(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def file_fingerprint(pdf_path: Path) -> str:
    """Cheap mtime+size fingerprint -- a single stat() call, no file read.
    See database.get_document_fingerprint() for why this matters at scale."""
    stat = pdf_path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _normalize_line_for_repeat_check(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", text.strip().lower())
    return re.sub(r"\d+", "#", text).strip()


def _strip_repeating_page_lines(pages: list[Page]) -> None:
    """Mutates pages in place, removing lines that repeat (punctuation/
    digit-normalized) across a large fraction of the document's pages --
    a running header/title-block table included as literal page content
    on every page, not just an occasional heading-detection artifact.
    Confirmed live: a real document's title-block ("FUNGSI: ... NOMOR:
    ... JUDUL: ... BERLAKU TMT: 1 November 2024 ... HALAMAN: X dari 48")
    repeated on nearly every page, so a query whose answer happens to
    live inside that boilerplate (the document's own effective-date
    field) retrieved 4 near-duplicate copies of the same jumbled
    two-column table instead of unique content -- drowning out retrieval
    diversity and giving the LLM nothing but repeated noise to extract
    the one requested field from. Same repetition-detection principle as
    _filter_repeating_headers() above, applied to raw chunkable page
    content instead of the derived headings list, with punctuation
    stripped too (not just digits) since OCR renders this boilerplate
    with slightly different stray punctuation on every page.

    Only DUPLICATE occurrences are stripped -- the first one is always
    kept. A running header often carries a genuine document-wide fact
    (an effective date, a revision number), not just decoration; that
    fact is only "repeated" because a header rewrites the same true
    value on every page, not because it's noise. Confirmed live: an
    earlier version of this function stripped a document's "BERLAKU TMT"
    (effective-date) line entirely as repeating boilerplate, and the
    only date left in context was an unrelated approval-sheet signing
    date, which the model then wrongly reported as the effective date.
    Keeping exactly one copy still eliminates the retrieval-diversity
    problem (duplicate chunks crowding out unique content) without
    deleting the only place a real fact was recorded."""
    if len(pages) < 4:
        return
    counts: dict[str, int] = {}
    for page in pages:
        for line in page.lines:
            counts[_normalize_line_for_repeat_check(line.text)] = (
                counts.get(_normalize_line_for_repeat_check(line.text), 0) + 1
            )
    threshold = max(3, len(pages) // 2)
    repeating = {norm for norm, c in counts.items() if norm and c >= threshold}

    kept_once: set[str] = set()
    for page in pages:
        kept = []
        changed = False
        for line in page.lines:
            norm = _normalize_line_for_repeat_check(line.text)
            if norm in repeating:
                if norm in kept_once:
                    changed = True
                    continue
                kept_once.add(norm)
            kept.append(line)
        if changed:
            page.lines = kept
            page.full_text = "\n".join(l.text for l in kept)


# ---------------------------------------------------------------------------
# Full ingestion of a single file
# ---------------------------------------------------------------------------

def extract_ingest_data(pdf_path: Path) -> dict:
    """CPU-bound half of ingestion: PDF parsing, OCR, heading detection, and
    chunking -- no Chroma, no embedder, no sqlite. Touches nothing shared, so
    it's the half that's safe to fan out across worker processes; the actual
    writes stay serialized (see write_ingest_data() and its callers).

    One exception: a page flagged graphical (see _page_is_graphical) makes a
    network call to the local Ollama server for a VLM description. Still
    safe from multiple worker processes -- Ollama itself serializes/queues
    concurrent requests -- but slower than pure CPU work, and rare enough
    (only genuinely diagram-heavy pages) that it doesn't change the overall
    parallelization strategy."""
    source = pdf_path.name
    pages = extract_pdf_pages(pdf_path)
    _strip_repeating_page_lines(pages)

    headings = detect_headings(pages)

    headings_by_page: dict[int, list[str]] = {}
    for h in headings:
        headings_by_page.setdefault(h["page"], []).append(h["text"])

    all_chunks: list[dict] = []
    ocr_confidence_by_page: dict[int, Optional[float]] = {}
    for page in pages:
        chunks = chunk_page_text(page.full_text, page.page_num, headings_by_page.get(page.page_num))
        all_chunks.extend(chunks)
        ocr_confidence_by_page[page.page_num] = page.ocr_confidence

    return {
        "source": source,
        "headings": headings,
        "all_chunks": all_chunks,
        "ocr_confidence_by_page": ocr_confidence_by_page,
    }


def _chunk_log_records(all_chunks: list[dict], ocr_confidence_by_page: dict[int, Optional[float]]) -> list[dict]:
    return [
        {
            "page": c["page"],
            "chunk_index": c["chunk_index"],
            "ocr_confidence": ocr_confidence_by_page.get(c["page"]),
            "text": c["text"],
        }
        for c in sorted(all_chunks, key=lambda c: (c["page"], c["chunk_index"]))
    ]


def write_chunk_log_md(source: str, records: list[dict]) -> Path:
    """Human-readable per-document Markdown log under data/chunk_logs/,
    rewritten in full every time this source is (re-)ingested -- same idea
    as database._write_session_log_file for chat history: Chroma stays the
    real source of truth, this is just an always-current, easy-to-open view
    of exactly what ingestion produced, for checking OCR/chunking quality
    without querying anything."""
    config.CHUNK_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.CHUNK_LOGS_DIR / f"{source}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {source}\n\n{len(records)} chunks\n\n")
        for r in records:
            conf = r["ocr_confidence"]
            conf_note = "not OCR'd" if conf is None or conf < 0 else f"OCR confidence {conf:.1f}"
            f.write(f"## Page {r['page']}, chunk {r['chunk_index']} ({conf_note})\n\n")
            f.write("```\n" + r["text"].strip() + "\n```\n\n")
    return out_path


def remove_chunk_log(source: str) -> None:
    (config.CHUNK_LOGS_DIR / f"{source}.md").unlink(missing_ok=True)


def write_ingest_data(pdf_path: Path, data: dict, collection, embedder) -> int:
    """Chroma/sqlite-writing half of ingestion. Must run on whatever single
    thread the rest of the app already funnels Chroma access through (see
    retrieval._chroma_thread) -- never call this from more than one thread
    or process at a time."""
    source = data["source"]
    headings = data["headings"]
    all_chunks = data["all_chunks"]
    ocr_confidence_by_page = data["ocr_confidence_by_page"]

    database.replace_headings(source, headings)

    # remove any previously-indexed chunks for this source before re-adding
    try:
        collection.delete(where={"source": source})
    except Exception:
        pass

    if all_chunks:
        texts = [c["text"] for c in all_chunks]
        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        ids = [f"{source}::{c['page']}::{c['chunk_index']}" for c in all_chunks]
        metadatas = [
            {
                "source": source,
                "page": c["page"],
                "chunk_index": c["chunk_index"],
                # -1 sentinel means "not OCR'd" -- Chroma metadata can't
                # hold None, and this keeps OCR confidence a visible,
                # queryable signal distinct from LLM-fabrication risk.
                "ocr_confidence": ocr_confidence_by_page.get(c["page"]) if ocr_confidence_by_page.get(c["page"]) is not None else -1.0,
            }
            for c in all_chunks
        ]
        collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        write_chunk_log_md(source, _chunk_log_records(all_chunks, ocr_confidence_by_page))
    else:
        remove_chunk_log(source)

    database.upsert_document(source, content_hash(pdf_path), file_fingerprint(pdf_path))
    return len(all_chunks)


def ingest_file(pdf_path: Path, collection, embedder) -> int:
    data = extract_ingest_data(pdf_path)
    return write_ingest_data(pdf_path, data, collection, embedder)


def remove_file(source: str, collection) -> None:
    try:
        collection.delete(where={"source": source})
    except Exception:
        pass
    remove_chunk_log(source)
    database.delete_document(source)
