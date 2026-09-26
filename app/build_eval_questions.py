"""Generates a large, diverse eval_questions.json from the REAL indexed
corpus -- every question's ground truth (expected_source/page/text or
expected_items) comes directly from what's actually stored in Chroma/
sqlite, never invented, so the confusion matrix this feeds into measures
real system behavior against real documents.

Three categories, same shape evaluate_grounded.py already expects:
  - answerable: a real stored heading's own chunk text becomes the
    expected answer content, question is "apa isi <heading> pada dokumen
    <source>?" -- the most direct way to ask about something we KNOW is in
    the corpus, at a specific verifiable location.
  - should_refuse: topics genuinely foreign to an internal SOP/contract
    corpus (weather, celebrities, cooking, sports scores, foreign
    geography) -- the system has no way to have real content for these,
    so refusing is the only correct behavior. Also includes a few
    "wrong identifier number" questions naming a real document but a
    section number confirmed NOT to exist in its heading list.
  - enumerate: a (source, leading-category-word) pair with >=2 distinct
    real headings becomes a "sebutkan semua X di dokumen Y" question,
    expected_items are the real heading texts themselves.

Stratified sampling across corpus prefixes (A-/B-/C-/D-/numbered/other) so
the question set actually covers a diverse slice of a 1000+ file corpus,
not just whichever files happen to sort first.

Usage: python -m app.build_eval_questions [target_answerable] [target_refuse] [target_enumerate]
Defaults: 60 answerable, 25 refuse, 15 enumerate (~100 total).
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict

from app import config, database, retrieval

OUT_PATH = config.DATA_DIR / "eval_questions.json"

# Topics with zero plausible overlap with an internal drilling-company
# SOP/contract corpus -- genuinely foreign, not just "uncommon."
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
]

_HEADING_LEAD_RE = re.compile(r"^([A-Za-z]+)\b")


def _lead_word(text: str) -> str:
    m = _HEADING_LEAD_RE.match((text or "").strip())
    return m.group(1).lower() if m else ""


# Structural noise filters -- the heading detector occasionally produces
# fragments that are real detections but useless as a QUESTION TARGET:
# too-short garbage ("Li"), signature-table labels ("2 Penyusun"), or a
# merge of several distinct headings glued into one run-on string. None of
# these are corpus-vocabulary-specific; they're generic shape checks.
_SIGNATURE_LABEL_WORDS = {
    "penyusun", "diperiksa", "disetujui", "diketahui", "mengetahui",
    "menyetujui", "memeriksa", "paraf", "tanggal", "nama", "jabatan",
}


def _looks_scrambled(word: str) -> bool:
    """ALL-CAPS ('KLASIFIKASI') and Title Case ('Klasifikasi') are both
    normal heading styles in this corpus -- neither is scrambled. An
    IRREGULAR mix, an uppercase letter appearing mid-word in an otherwise
    lowercase token ('eemeCnatt', 'idaanrS'), almost never happens in
    genuine Indonesian/English prose -- it's the signature of OCR
    table-column text getting interleaved into garbage. Generic shape
    check, not tied to any specific corpus word."""
    if word.isupper() or word.islower():
        return False
    if word[0].isupper() and word[1:].islower():
        return False  # ordinary Title Case
    return any(c.isupper() for c in word[1:])


def _is_usable_heading(text: str) -> bool:
    words = text.strip().split()
    if len(words) < 2:
        return False  # "Li", a bare number, etc -- not enough to ask about
    alpha_words = [w for w in words if any(c.isalpha() for c in w)]
    if len(alpha_words) < 2:
        return False
    scrambled = sum(1 for w in alpha_words if _looks_scrambled(w))
    if scrambled / len(alpha_words) > 0.15:
        return False  # OCR-scrambled garbage, not real readable text
    if max(len(w) for w in alpha_words) < 4:
        return False  # every word is short noise ("BIP Poo 4ni") -- no real content word at all
    lead = _lead_word(text)
    if lead in _SIGNATURE_LABEL_WORDS:
        return False  # a signature-sheet field label, not real section content
    if lead and text.lower().count(lead) >= 2:
        return False  # likely several distinct headings merged into one run-on string
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


def build_answerable(target: int, sample_sources: list[str]) -> list[dict]:
    grouped = database.get_all_headings_grouped_by_source()
    collection = retrieval.get_collection()
    out: list[dict] = []
    rng = random.Random(7)

    candidates = []
    for source in sample_sources:
        headings = [h for h in grouped.get(source, []) if _is_usable_heading(h["text"])]
        rng.shuffle(headings)
        for h in headings[:3]:  # at most 3 questions per document, for diversity across files
            candidates.append((source, h))
    rng.shuffle(candidates)

    for source, h in candidates:
        if len(out) >= target:
            break
        try:
            got = collection.get(
                where={"$and": [{"source": source}, {"page": h["page"]}]},
                include=["documents"],
            )
        except Exception:
            continue
        docs = got.get("documents", [])
        if not docs:
            continue
        chunk_text = max(docs, key=len)  # the fullest chunk on that page
        expected_text = _snippet(chunk_text)
        if len(expected_text.split()) < 8:
            continue  # too thin to score overlap meaningfully

        heading_clean = h["text"].strip()
        question = f"Apa isi bagian '{heading_clean}' pada dokumen {source}?"
        out.append(
            {
                "question": question,
                "should_refuse": False,
                "expected_source": source,
                "expected_page": h["page"],
                "expected_text": expected_text,
            }
        )
    return out


def build_should_refuse(target: int, sample_sources: list[str]) -> list[dict]:
    out: list[dict] = []
    rng = random.Random(13)
    off_topic = list(_OFF_TOPIC_QUESTIONS)
    rng.shuffle(off_topic)
    for q in off_topic:
        if len(out) >= target:
            break
        out.append({"question": q, "should_refuse": True, "expected_source": None,
                     "expected_page": None, "expected_text": None})

    if len(out) >= target:
        return out

    # Fill remaining with "wrong identifier" questions against real documents:
    # a number confirmed absent from that document's own real headings.
    grouped = database.get_all_headings_grouped_by_source()
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
                "should_refuse": True,
                "expected_source": None,
                "expected_page": None,
                "expected_text": None,
            }
        )
    return out


def build_enumerate(target: int, sample_sources: list[str]) -> list[dict]:
    grouped = database.get_all_headings_grouped_by_source()
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
                "should_refuse": False,
                "is_enumerate": True,
                "expected_items": items,
                "expected_source": source,
            }
        )
    return out


def main() -> None:
    database.init_db()
    target_answerable = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    target_refuse = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    target_enumerate = int(sys.argv[3]) if len(sys.argv) > 3 else 15

    all_sources = database.list_document_sources()
    print(f"corpus has {len(all_sources)} documents")

    # oversample the source pool so build_answerable/build_enumerate have
    # enough raw material even after per-file/per-word filtering
    sample = _stratified_sample(all_sources, min(len(all_sources), max(200, target_answerable * 3)))
    print(f"sampled {len(sample)} documents, stratified across prefixes")

    answerable = build_answerable(target_answerable, sample)
    refuse = build_should_refuse(target_refuse, sample)
    enumerate_qs = build_enumerate(target_enumerate, sample)

    questions = answerable + refuse + enumerate_qs
    print(f"built {len(answerable)} answerable, {len(refuse)} should_refuse, "
          f"{len(enumerate_qs)} enumerate = {len(questions)} total")

    doc_coverage = len({q["expected_source"] for q in questions if q.get("expected_source")})
    print(f"distinct documents covered: {doc_coverage}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(questions, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written to {OUT_PATH}")


if __name__ == "__main__":
    main()
