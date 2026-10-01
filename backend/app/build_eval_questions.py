"""Generates a large, diverse eval_questions.json from the REAL indexed
corpus -- every question's ground truth (expected_source/page/text,
expected_items, or expected_sources) comes directly from what's actually
stored in Chroma/sqlite, never invented, so the confusion matrix this feeds
into measures real system behavior against real documents.

Five categories, evaluated in evaluate_grounded.py:
  1. answerable: a real stored heading's own chunk text becomes expected
     content, question quotes the heading: "Apa isi bagian '<heading>'
     pada dokumen <source>?". Tests exact-heading retrieval.
  2. procedural: instructional/procedural sections (procedures, step-by-step,
     numbered processes). The heading is paraphrased into a natural-language
     instruction ("Bagaimana prosedur <topic> pada dokumen <source>?" /
     "Jelaskan langkah-langkah <topic> pada dokumen <source>.") WITHOUT
     verbatim quotes, forcing semantic retrieval to engage instead of the
     exact-quote-boost shortcut.
  3. cross_document: cross-document discovery queries ("Dokumen mana saja
     yang membahas <topic>?"). Topic is a distinct non-generic subject appearing
     in a small, countable set of real documents (2-6 documents). Evaluated
     by source set overlap, precision, and recall.
  4. should_refuse: topics genuinely foreign to an internal SOP/contract
     corpus (weather, celebrities, cooking, etc.), plus "wrong identifier"
     questions referencing a real document but a non-existent section number.
  5. enumerate: a (source, leading-category-word) pair with >=2 distinct
     real headings becomes "Sebutkan semua X di dokumen Y". Scored on
     completeness and exact match.

Stratified sampling across corpus prefixes (A-/B-/C-/D-/numbered/other) ensures
broad file coverage across all 1,177 documents in the corpus.

Usage:
  python -m app.build_eval_questions [target_total]
  or:
  python -m app.build_eval_questions [answerable] [procedural] [cross_doc] [refuse] [enumerate]
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from app import config, database, retrieval

OUT_PATH = config.DATA_DIR / "eval_questions.json"

_OFF_TOPIC_QUESTIONS = [
    "Apa resep membuat rendang yang enak?",
    "Siapa pemenang Piala Dunia 2022?",
    "Bagaimana cara merawat kucing peliharaan?",
    "Apa ibu kota negara Prancis?",
    "Bagaimana cuaca di Tokyo hari ini?",
    "Siapa aktor utama film Avengers terbaru?",
    "Apa resep kue coklat yang mudah?",
    "Bagaimana cara bermain gitar untuk pemula?",
    "Apa saja destinasi wisata terkenal di Bali?",
    "Siapa penyanyi paling terkenal di Indonesia saat ini?",
    "Bagaimana sejarah Kerajaan Majapahit?",
    "Apa manfaat olahraga lari setiap pagi?",
    "Bagaimana cara menanam tomat di pot?",
    "Siapa presiden pertama Amerika Serikat?",
    "Apa perbedaan antara kucing dan anjing sebagai hewan peliharaan?",
    "Bagaimana cara kerja mesin roket luar angkasa?",
    "Apa makanan khas tradisional Yogyakarta?",
    "Siapa penemu lampu pijar listrik?",
    "Bagaimana aturan permainan catur internasional?",
    "Apa penyebab terjadinya gerhana matahari total?",
]

_HEADING_LEAD_RE = re.compile(r"^([A-Za-z]+)\b")
_PREFIX_NUMBERING_RE = re.compile(
    r"^(\d+(\.\d+)*|[A-Za-z]\.|[IVXLCDMivxlcdm]+\.|\bBAB\s+[IVXLCDM\d]+|\bPASAL\s+\d+)\s*[-.:)]?\s*",
    re.IGNORECASE,
)

_TABLE_COLUMN_NOISE_RE = re.compile(
    r"\b(no|nomor)\s+(pekerjaan|proyek|pemilik|nilai|uraian|kegiatan|item|revisi)\b",
    re.IGNORECASE,
)


_PROCEDURE_KEYWORD_RE = re.compile(
    r"\b(prosedur|langkah|cara|tata cara|instruksi|tahapan|pelaksanaan|alur|proses|"
    r"pengoperasian|pemeriksaan|penanganan|pemasangan|pengujian|pemeliharaan|"
    r"pengawasan|pengajuan|penerimaan|penyimpanan|pembersihan|pencegahan|"
    r"penanggulangan|pengelolaan|evakuasi|inspeksi|perbaikan|penggunaan|pembuatan|"
    r"penggantian|pengendalian|verifikasi|validasi|penyelidikan|pelaporan)\b",
    re.IGNORECASE,
)

_STEP_MARKER_RE = re.compile(
    r"(?m)^\s*(?:\d+[\.)]\s+|[a-e][\.)]\s+|\b(?:langkah|tahap|instruksi|urutan)\b)",
    re.IGNORECASE,
)

# Reject document header box boilerplate and signature table artifacts
_HEADER_NOISE_RE = re.compile(
    r"(berlaku\s+tmt|nomor\s*:|halaman\s*:|revisi\s*ke|judul\s*:|fungsi\s*:|"
    r"hal\.\s*\d+|dari\s+\d+|lembar\s+persetujuan|catatan\s+perubahan|"
    r"pertamina\s+drilling|tata\s+kerja\s+organisasi|tata\s+kerja\s+individu|"
    r"tanda\s+tangan|narasumber|disiapkan\s+oleh|disetujui\s+oleh|diperiksa\s+oleh|"
    r"diubah\s+oleh|diketahui\s+oleh|revisike|tmt\s*:)",
    re.IGNORECASE,
)

_SIGNATURE_WORDS = {
    "penyusun", "diperiksa", "disetujui", "diketahui", "mengetahui",
    "menyetujui", "memeriksa", "paraf", "tanggal", "nama", "jabatan",
    "narasumber", "initial", "manager", "direktur", "vice", "president",
}

_GENERIC_HEADINGS = {
    "daftar isi", "ruang lingkup", "maksud dan tujuan", "tujuan", "definisi",
    "pengertian", "referensi", "penutup", "lampiran", "lembar pengesahan",
    "riwayat perubahan", "bagan alir", "tanggung jawab", "dasar hukum",
    "ketentuan umum", "lingkup kerja", "latar belakang", "dokumen terkait",
    "tinjauan", "catatan", "struktur organisasi", "pendahuluan", "umum",
    "istilah dan singkatan", "landasan hukum", "daftar singkatan",
    "dokumen rujukan", "sasaran", "kebijakan", "pedoman umum",
}


_LEADING_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _heading_depth(text: str) -> int:
    """Sub-heading nesting depth from a leading numeric prefix ('5.2.1' -> 3,
    '5.2' -> 2, unnumbered -> 0). Used to prefer deeper, harder-to-pinpoint
    sections when building answerable/procedural questions -- this targets
    the eval's own known weak spot (page-hit rate 64-68%, well below
    document-hit rate) instead of adding arbitrary difficulty."""
    m = _LEADING_NUMBER_RE.match((text or "").strip())
    return m.group(1).count(".") + 1 if m else 0


def _lead_word(text: str) -> str:
    m = _HEADING_LEAD_RE.match((text or "").strip())
    word = m.group(1).lower() if m else ""
    return word if len(word) >= 3 else ""


def _looks_scrambled(word: str) -> bool:
    if word.isupper() or word.islower():
        return False
    if word[0].isupper() and word[1:].islower():
        return False
    return any(c.isupper() for c in word[1:])


# Consonant pairs that essentially never occur adjacently in real Indonesian
# words (not even in loanwords -- unlike e.g. "bl"/"kl"/"tr" which are common
# via absorbed loanwords). Found by tracing a real corrupted eval question:
# "daftalra mpiran" vs. the real heading "daftar lampiran" is the identical
# 14 letters with the word-boundary space shifted and the adjacent "r"/"l"
# transposed across it -- a word-boundary-reconstruction artifact (see
# ingestion.py's OCR line-rebuilding), tagged by the "lr" cluster it produces.
_IMPROBABLE_CLUSTERS = ("lr", "rl")


def _has_improbable_cluster(word: str) -> bool:
    lw = word.lower()
    return any(c in lw for c in _IMPROBABLE_CLUSTERS)


def _is_usable_heading(text: str) -> bool:
    if not text:
        return False
    raw = text.strip()
    if _HEADER_NOISE_RE.search(raw):
        return False
    if "..." in raw or "scsec" in raw.lower() or "|" in raw or "~" in raw:
        return False
    # Reject OCR header-box remnants: short phrases containing company names / codes
    if re.search(r"\b(pertamina|pdsi|drilling)\b", raw, re.IGNORECASE):
        return False
    # Reject fragments that start with punctuation or look like partial document codes
    if raw.startswith(".") or raw.startswith(":") or re.match(r"^[A-Z]\s+\d+\s+[A-Z]", raw):
        return False
    words = raw.split()
    if len(words) < 2 or len(words) > 10:
        return False
    alpha_words = [w for w in words if any(c.isalpha() for c in w)]
    if len(alpha_words) < 2:
        return False
    scrambled = sum(1 for w in alpha_words if _looks_scrambled(w))
    if scrambled / len(alpha_words) > 0.15:
        return False
    if any(_has_improbable_cluster(w) for w in alpha_words):
        return False
    if max(len(w) for w in alpha_words) < 4:
        return False
    lead = _lead_word(raw)
    if lead in _SIGNATURE_WORDS:
        return False
    if lead and raw.lower().count(lead) >= 2:
        return False
    return True



def _prefix_bucket(source: str) -> str:
    ch = source[0].upper() if source else "?"
    if ch in "ABCD":
        return ch
    if ch.isdigit():
        return "NUM"
    return "OTHER"


def _stratified_sample(sources: list[str], n: int, seed: int = 42) -> list[str]:
    rng = random.Random(seed)
    buckets: dict[str, list[str]] = defaultdict(list)
    for s in sources:
        buckets[_prefix_bucket(s)].append(s)
    for v in buckets.values():
        rng.shuffle(v)

    picked: list[str] = []
    bucket_names = list(buckets.keys())
    i = 0
    while len(picked) < n and any(buckets.values()):
        b = bucket_names[i % len(bucket_names)]
        if buckets[b]:
            picked.append(buckets[b].pop())
        i += 1
    return picked


def _snippet(text: str, max_words: int = 60) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def _clean_topic_from_heading(text: str) -> str:
    cleaned = _PREFIX_NUMBERING_RE.sub("", text.strip()).strip()
    cleaned = re.sub(
        r"^(prosedur|tata cara|langkah-langkah|langkah|instruksi kerja|petunjuk teknis|petunjuk)\s*[-:]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


def build_answerable(target: int, sample_sources: list[str], grouped_headings: dict | None = None) -> list[dict]:
    grouped = grouped_headings if grouped_headings is not None else database.get_all_headings_grouped_by_source()
    collection = retrieval.get_collection()
    out: list[dict] = []
    rng = random.Random(7)

    candidates = []
    for source in sample_sources:
        headings = [h for h in grouped.get(source, []) if _is_usable_heading(h["text"])]
        if headings:
            rng.shuffle(headings)
            # Prefer a deeper sub-heading over a top-level one when available --
            # harder to pinpoint (targets the eval's own known weak spot: page-
            # hit rate 64-68% vs. document-hit rate 85-88%), not arbitrary
            # difficulty. Falls back to whatever's available when nothing is
            # numbered/nested.
            best = max(headings, key=lambda h: _heading_depth(h["text"]))
            candidates.append((source, best))
    rng.shuffle(candidates)

    for source, h in candidates:
        if len(out) >= target:
            break
        try:
            got = retrieval.run_on_chroma_thread(
                lambda s=source, p=h["page"]: collection.get(
                    where={"$and": [{"source": s}, {"page": p}]},
                    include=["documents"],
                )
            )
        except Exception:
            continue
        docs = got.get("documents", [])
        if not docs:
            continue
        chunk_text = max(docs, key=len)
        expected_text = _snippet(chunk_text)
        if len(expected_text.split()) < 10:
            continue

        heading_clean = h["text"].strip()
        # Half the questions drop the filename entirely -- a real user
        # rarely knows which of 1177 files holds the answer. Same expected
        # source/page (we still know ground truth), but now the system must
        # find it through retrieval alone instead of an exact-filename
        # boost, which is the realistic and harder case.
        no_filename = rng.random() < 0.5
        if no_filename:
            question = f"Apa isi bagian '{heading_clean}'?"
        else:
            question = f"Apa isi bagian '{heading_clean}' pada dokumen {source}?"
        out.append(
            {
                "question": question,
                "category": "answerable",
                "should_refuse": False,
                "expected_source": source,
                "expected_page": h["page"],
                "expected_text": expected_text,
                "variant": "no_filename" if no_filename else "with_filename",
            }
        )
    return out


def build_procedural(target: int, sample_sources: list[str], grouped_headings: dict | None = None) -> list[dict]:
    """Build natural-language instructional/procedural questions without quotes.
    For sections with procedural headings or numbered step lists, generate questions
    like 'Bagaimana prosedur <topic> pada dokumen <source>?' or
    'Jelaskan langkah-langkah <topic> pada dokumen <source>.'
    Requires real semantic retrieval rather than exact-quote-boost."""
    grouped = grouped_headings if grouped_headings is not None else database.get_all_headings_grouped_by_source()
    collection = retrieval.get_collection()
    out: list[dict] = []
    rng = random.Random(19)

    candidates = []
    for source in sample_sources:
        headings = [
            h for h in grouped.get(source, [])
            if _is_usable_heading(h["text"]) and _PROCEDURE_KEYWORD_RE.search(h["text"])
        ]
        if headings:
            rng.shuffle(headings)
            best = max(headings, key=lambda h: _heading_depth(h["text"]))
            candidates.append((source, best))
    rng.shuffle(candidates)

    templates = [
        "Bagaimana prosedur {topic} pada dokumen {source}?",
        "Jelaskan langkah-langkah {topic} pada dokumen {source}.",
        "Jelaskan tata cara {topic} sesuai dokumen {source}.",
        "Bagaimana tahapan pelaksanaan {topic} pada dokumen {source}?",
        "Jelaskan cara {topic} pada dokumen {source}.",
    ]

    for source, h in candidates:
        if len(out) >= target:
            break
        try:
            got = retrieval.run_on_chroma_thread(
                lambda s=source, p=h["page"]: collection.get(
                    where={"$and": [{"source": s}, {"page": p}]},
                    include=["documents"],
                )
            )
        except Exception:
            continue
        docs = got.get("documents", [])
        if not docs:
            continue
        chunk_text = max(docs, key=len)
        expected_text = _snippet(chunk_text)
        if len(expected_text.split()) < 10:
            continue

        raw_heading = h["text"].strip()
        topic = _clean_topic_from_heading(raw_heading)
        topic = re.sub(r"^[0-9\s.:/-]+", "", topic).strip()
        words = topic.split()
        if len(words) < 2 or len(words) > 8:
            continue
        if _TABLE_COLUMN_NOISE_RE.search(topic):
            continue

        topic_lower = topic.lower()
        if topic_lower.startswith("langkah"):
            question = f"Jelaskan {topic_lower} pada dokumen {source}."
        elif topic_lower.startswith("tata cara"):
            question = f"Jelaskan {topic_lower} sesuai dokumen {source}."
        elif topic_lower.startswith("tahapan") or topic_lower.startswith("tahap"):
            question = f"Bagaimana {topic_lower} pada dokumen {source}?"
        elif topic_lower.startswith("prosedur") or topic_lower.startswith("cara"):
            question = f"Bagaimana {topic_lower} pada dokumen {source}?"
        elif topic_lower.startswith("pelaksanaan"):
            question = f"Bagaimana {topic_lower} pada dokumen {source}?"
        elif topic_lower.startswith("instruksi"):
            question = f"Jelaskan {topic_lower} pada dokumen {source}."
        else:
            tmpl = templates[len(out) % len(templates)]
            question = tmpl.format(topic=topic_lower, source=source)

        # Same no-filename split as build_answerable() -- every template
        # above ends with "(pada|sesuai) dokumen {source}[.?]", so stripping
        # that clause and keeping the original terminator reliably produces
        # the realistic, harder no-filename phrasing without a second
        # template set to maintain. "\S+" for the filename, not a
        # "not dot/question-mark" class -- confirmed live these real
        # filenames are full of periods themselves (dates like
        # "01.10.2020" embedded in the name), which silently made the
        # original character-class version match 0/40 questions.
        variant = "with_filename"
        if rng.random() < 0.5:
            no_fn = re.sub(r"\s*(pada|sesuai) dokumen \S+([.?])$", r"\2", question)
            if no_fn != question:
                question = no_fn
                variant = "no_filename"

        out.append(
            {
                "question": question,
                "category": "procedural",
                "is_procedural": True,
                "should_refuse": False,
                "variant": variant,
                "expected_source": source,
                "expected_page": h["page"],
                "expected_text": expected_text,
                "original_heading": raw_heading,
            }
        )
    return out


def build_cross_document(target: int, all_sources: list[str], grouped_headings: dict | None = None) -> list[dict]:
    """Generates cross-document discovery questions ('Dokumen mana saja yang membahas <topic>?').
    Selects topics appearing in a small, countable set of real documents (2-6 documents).
    Expected ground truth is the set of all matching documents (expected_sources)."""
    grouped = grouped_headings if grouped_headings is not None else database.get_all_headings_grouped_by_source()
    topic_to_sources: dict[str, set[str]] = defaultdict(set)

    # Words that are common abbreviations or too generic to be a real topic
    _TOPIC_REJECT_WORDS = {
        "ya", "mp", "pt", "pp", "no", "hal", "rev", "dst", "dll", "sda", "tsb",
        "dkk", "ket", "kol", "vol", "ver", "ref", "sub", "std", "def",
    }

    for source in all_sources:
        for h in grouped.get(source, []):
            if not _is_usable_heading(h["text"]):
                continue
            cleaned = _PREFIX_NUMBERING_RE.sub("", h["text"].strip()).strip().lower()
            # Remove common leading doc-type prefixes
            cleaned = re.sub(
                r"^(prosedur|tata cara|pedoman|petunjuk teknis|instruksi kerja)\s*[-:]?\s*",
                "",
                cleaned,
            ).strip()
            # Strip trailing punctuation / colon remnants
            cleaned = re.sub(r"[\s:.,;]+$", "", cleaned).strip()
            words = cleaned.split()
            if len(words) < 2 or len(words) > 5:
                continue
            if cleaned in _GENERIC_HEADINGS or _TABLE_COLUMN_NOISE_RE.search(cleaned):
                continue
            # Reject topics where any word is a known noise abbreviation
            if any(w in _TOPIC_REJECT_WORDS for w in words):
                continue
            # Reject topics that contain a 2-letter uppercase word (abbreviation artifact like "MP", "YA")
            if any(len(w) <= 2 and w.isalpha() for w in cleaned.split()):
                continue
            # Reject topics with trailing punctuation or that look like number references
            if re.search(r"\d{4}|rev\s*\d|\.\s*$", cleaned):
                continue
            topic_to_sources[cleaned].add(source)


    valid_topics = [
        (topic, sorted(srcs))
        for topic, srcs in topic_to_sources.items()
        if 3 <= len(srcs) <= 5
    ]

    # Broaden ground truth using the system's own retrieval, grounded in real
    # chunk text -- not invented. Root cause this addresses (found by tracing
    # 4 real "wrong discovery" cases in the 2026-09-29 eval): the strict
    # heading-co-occurrence set is a weak, incomplete proxy for topical
    # relevance -- e.g. asked about "rigging down", the strict set listed 4
    # incidental MWD-tool docs sharing that heading, but a whole document
    # titled RIG_DOWN_RIG.PDF (genuinely about the topic) wasn't in it at
    # all. A document is now also added to a topic's expected set if the
    # topic phrase appears verbatim in one of its OWN retrieved chunks --
    # i.e. the system's real embedding search surfaces the topic's exact
    # words inside that document's actual text, not just a shared heading.
    expanded_topics = []
    for topic, strict_srcs in valid_topics:
        broadened = set(strict_srcs)
        try:
            hits = retrieval.retrieve(topic, k=20)
        except Exception:
            hits = []
        for h in hits:
            if h["source"] not in broadened and topic in h["text"].lower():
                broadened.add(h["source"])
        expanded_topics.append((topic, sorted(broadened)))
    valid_topics = expanded_topics

    rng = random.Random(31)
    rng.shuffle(valid_topics)

    templates = [
        "Dokumen mana saja yang membahas tentang {topic}?",
        "Dokumen apa saja yang berkaitan dengan {topic}?",
        "Sebutkan dokumen yang memuat pedoman tentang {topic}.",
        "Dokumen mana yang mengatur mengenai {topic}?",
    ]

    out: list[dict] = []
    for topic, srcs in valid_topics:
        if len(out) >= target:
            break
        tmpl = templates[len(out) % len(templates)]
        question = tmpl.format(topic=topic)
        out.append(
            {
                "question": question,
                "category": "cross_document",
                "is_cross_doc": True,
                "should_refuse": False,
                "expected_sources": srcs,
                "topic": topic,
                "expected_source": None,
                "expected_page": None,
                "expected_text": None,
            }
        )
    return out


def build_should_refuse(target: int, sample_sources: list[str], grouped_headings: dict | None = None) -> list[dict]:
    out: list[dict] = []
    rng = random.Random(13)
    off_topic = list(_OFF_TOPIC_QUESTIONS)
    rng.shuffle(off_topic)
    for q in off_topic:
        if len(out) >= target:
            break
        out.append({
            "question": q,
            "category": "should_refuse",
            "should_refuse": True,
            "expected_source": None,
            "expected_page": None,
            "expected_text": None,
        })

    if len(out) >= target:
        return out

    grouped = grouped_headings if grouped_headings is not None else database.get_all_headings_grouped_by_source()
    remaining_sources = list(sample_sources)
    rng.shuffle(remaining_sources)
    for source in remaining_sources:
        if len(out) >= target:
            break
        headings = grouped.get(source, [])
        used_numbers = set()
        for h in headings:
            m = re.search(r"\b(\d+)\b", h["text"])
            if m:
                used_numbers.add(int(m.group(1)))
        fake_number = 9999
        while fake_number in used_numbers:
            fake_number -= 1
        out.append(
            {
                "question": f"Apa isi Pasal {fake_number} pada dokumen {source}?",
                "category": "should_refuse",
                "should_refuse": True,
                "expected_source": None,
                "expected_page": None,
                "expected_text": None,
            }
        )
    return out


def build_ambiguous_identifier(target: int, all_sources: list[str], grouped_headings: dict | None = None) -> list[dict]:
    """Bare generic-identifier questions ('Apa isi Pasal 8?') with NO document
    named, chosen only when that exact identifier appears in real headings
    across 2+ real documents -- the correct behavior is retrieval.
    detect_ambiguity()'s clarification request, never a guess. Scored inside
    the same should_refuse bucket (a bare unqualified answer here is wrong),
    but this is a genuinely different failure mode than should_refuse's
    off-topic/nonexistent questions: it specifically stress-tests the
    ambiguity-detection feature, which a generic refusal-phrase check alone
    can't tell apart from a real answer (see evaluate_grounded.py's
    _really_answered() for the matching scoring fix)."""
    grouped = grouped_headings if grouped_headings is not None else database.get_all_headings_grouped_by_source()
    id_to_sources: dict[str, set[str]] = defaultdict(set)
    for source in all_sources:
        for h in grouped.get(source, []):
            m = retrieval.GENERIC_IDENTIFIER_PATTERN.search(h["text"])
            if m:
                identifier = f"{m.group(1).lower()} {m.group(2)}"
                id_to_sources[identifier].add(source)

    candidates = [(ident, sorted(srcs)) for ident, srcs in id_to_sources.items() if len(srcs) >= 2]
    rng = random.Random(43)
    rng.shuffle(candidates)

    out: list[dict] = []
    for ident, srcs in candidates:
        if len(out) >= target:
            break
        out.append(
            {
                "question": f"Apa isi {ident.title()}?",
                "category": "ambiguous_identifier",
                "is_ambiguous_identifier": True,
                "should_refuse": True,
                "expected_source": None,
                "expected_page": None,
                "expected_text": None,
                "candidate_sources": srcs,
            }
        )
    return out


def build_whole_document(sources: list[str]) -> list[dict]:
    """One question per source, for documents with NO usable heading at all
    (scans, forms, cover pages) -- the heading-based categories above can
    never pick these no matter how many questions are generated, since they
    all filter candidates down to "has at least one usable heading" first.
    Routes through generate_answer()'s existing full-document-fallback path
    (a whole-document question with no specific identifier), so this needs
    no new system behavior, just a question shape that exercises it. Ground
    truth is just "the system should find and discuss this specific
    document", scored via exact_doc_hit only -- there's no heading/page to
    target."""
    out: list[dict] = []
    for source in sources:
        out.append(
            {
                "question": f"Apa isi dokumen {source} secara umum?",
                "category": "whole_document",
                "is_whole_document": True,
                "should_refuse": False,
                "expected_source": source,
            }
        )
    return out


def build_enumerate(target: int, sample_sources: list[str], grouped_headings: dict | None = None) -> list[dict]:
    grouped = grouped_headings if grouped_headings is not None else database.get_all_headings_grouped_by_source()
    out: list[dict] = []
    rng = random.Random(21)
    candidates = []
    for source in sample_sources:
        headings = [h for h in grouped.get(source, []) if _is_usable_heading(h["text"])]
        by_word: dict[str, list[str]] = defaultdict(list)
        for h in headings:
            w = _lead_word(h["text"])
            if w:
                by_word[w].append(h["text"].strip())
        for w, texts in by_word.items():
            distinct = sorted(set(texts))
            if len(distinct) >= 2:
                candidates.append((source, w, distinct))
    rng.shuffle(candidates)

    for source, word, items in candidates:
        if len(out) >= target:
            break
        out.append(
            {
                "question": f"Sebutkan semua {word} yang ada di dokumen {source}.",
                "category": "enumerate",
                "should_refuse": False,
                "is_enumerate": True,
                "expected_items": items,
                "expected_source": source,
            }
        )
    return out


def main() -> None:
    database.init_db()

    args = [int(x) for x in sys.argv[1:] if x.isdigit()]
    if len(args) == 5:
        target_answerable, target_procedural, target_cross_doc, target_refuse, target_enumerate = args
    elif len(args) == 3:
        target_answerable, target_refuse, target_enumerate = args
        target_procedural = 0
        target_cross_doc = 0
    elif len(args) == 1:
        total = args[0]
        if total <= 100:
            # Weighted toward answerable/procedural, which each guarantee one
            # fresh unique document per question -- pushes file coverage
            # toward the corpus size instead of reusing the same ~60 files
            # (v1's actual result). Should-refuse trimmed to keep space for
            # that without dropping the hallucination-safety check entirely.
            target_answerable = 45
            target_procedural = 28
            target_cross_doc = 15
            target_refuse = 8
            target_enumerate = 4
        elif total <= 250:
            target_answerable = 100
            target_procedural = 60
            target_cross_doc = 35
            target_refuse = 35
            target_enumerate = 20
        else:
            target_answerable = int(total * 0.45)
            target_procedural = int(total * 0.25)
            target_cross_doc = int(total * 0.10)
            target_refuse = int(total * 0.12)
            target_enumerate = total - (target_answerable + target_procedural + target_cross_doc + target_refuse)
    else:
        target_answerable = 45
        target_procedural = 28
        target_cross_doc = 15
        target_refuse = 8
        target_enumerate = 4

    all_sources = database.list_document_sources()
    print(f"Corpus has {len(all_sources)} documents")

    sample_pool_size = min(len(all_sources), max(300, (target_answerable + target_procedural) * 2))
    sample = _stratified_sample(all_sources, sample_pool_size)
    print(f"Sampled {len(sample)} documents stratified across prefixes")

    # Carve the ambiguous-identifier category out of the existing refuse
    # budget rather than adding a 6th CLI argument -- keeps the established
    # 1/3/5-arg call signature intact for existing scripts/notes.
    target_ambiguous = target_refuse // 3
    target_refuse -= target_ambiguous

    # Computed once and threaded through every builder instead of each of
    # the 6 calling it independently -- this scans EVERY document's
    # headings from the DB (not just the stratified sample), so 6 separate
    # calls meant 6x the same full-corpus DB work for no reason. Real
    # savings that scale directly with corpus size, most relevant for the
    # 1000-question build this is being done ahead of.
    grouped_headings = database.get_all_headings_grouped_by_source()

    answerable = build_answerable(target_answerable, sample, grouped_headings)
    procedural = build_procedural(target_procedural, sample, grouped_headings)
    cross_doc = build_cross_document(target_cross_doc, all_sources, grouped_headings)
    refuse = build_should_refuse(target_refuse, sample, grouped_headings)
    ambiguous = build_ambiguous_identifier(target_ambiguous, all_sources, grouped_headings)
    enumerate_qs = build_enumerate(target_enumerate, sample, grouped_headings)

    questions = answerable + procedural + cross_doc + refuse + ambiguous + enumerate_qs
    no_filename_count = sum(1 for q in questions if q.get("variant") == "no_filename")
    print(
        f"Built: {len(answerable)} answerable, {len(procedural)} procedural, "
        f"{len(cross_doc)} cross-doc, {len(refuse)} should_refuse, "
        f"{len(ambiguous)} ambiguous_identifier, {len(enumerate_qs)} enumerate "
        f"= {len(questions)} total ({no_filename_count} no-filename variants)"
    )

    doc_coverage = set()
    for q in questions:
        if q.get("expected_source"):
            doc_coverage.add(q["expected_source"])
        if q.get("expected_sources"):
            doc_coverage.update(q["expected_sources"])
        if q.get("candidate_sources"):
            doc_coverage.update(q["candidate_sources"])
    print(f"Distinct documents covered across all questions: {len(doc_coverage)} of {len(all_sources)}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(questions, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
