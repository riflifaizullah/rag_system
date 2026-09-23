"""Embedding-based retrieval, scoped heading lookups, named-document
targeting, ambiguity detection, and deterministic broad-category listing.

Carries fixes for known-bugs #1, #2, #7 from the rebuild spec:
  #1 retrieve() takes an optional source_filter and turns it into a Chroma
     `where` clause -- naming a document actually constrains the k-NN search.
  #2 heading lookups are always scoped to specific source(s), never the
     whole corpus.
  #7 broad-category word selection tries candidates in the order they
     appear in the question, not via unordered set iteration.

Later additions, layered onto the same retrieve() pipeline in this order:
  - reranking (get_reranker()/rerank()): fetch a wider embedding candidate
    set, then have a cross-encoder score (question, chunk) pairs jointly --
    catches cases where several near-identical boilerplate clauses tie on
    pure embedding similarity but only one is topically on point.
  - exact-identifier boost (_exact_identifier_boost()): overrides both of
    the above outright when the question names a specific clause/section
    number, because dense retrieval still doesn't key strongly on a literal
    number difference between otherwise-similar clauses.
  - the shorthand-matching regex (_build_shorthand_patterns()), the
    identifier-boost pattern, and the broad-category word detection
    (_category_candidates_from_headings()) are all built from real indexed
    content (filenames / retrieved chunk text / real headings) rather than
    a hardcoded vocabulary, on the theory that anything corpus-specific
    (document-type words, section-keyword words) will silently go stale
    the moment this is ported to a differently-named real document set.
    _GENERIC_IDENTIFIER_PATTERN (ambiguity detection) has NOT been
    generalized the same way yet -- still a fixed (pasal|lampiran|bab)
    list, same latent risk, just not hit by a real test case so far.
  - enumerate-intent routing (broad_category_listing(), document_type_
    listing()): "list all X" / "apa saja X" / "berapa banyak X" questions
    are answered by reading the real heading/document index directly and
    formatting deterministically, never through retrieve() at all --
    similarity search only returns the *most relevant* top-k chunks, never
    a completeness guarantee, so an enumerate question routed through it
    can (and did, live) silently return a partial list.
  - hybrid search (_bm25_search(), reciprocal rank fusion in retrieve()):
    a sparse/keyword BM25 pass runs alongside the embedding pass for the
    "specific"/"broad" paths (enumerate bypasses retrieval entirely), since
    embeddings alone can rank a chunk with the wrong literal clause number
    as more similar than the chunk with the right one on short,
    structurally-repetitive legal text.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Optional

import chromadb
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from app import config, database

_COLLECTION_NAME = "stk_documents"

_embedder: Optional[SentenceTransformer] = None
_chroma_client = None
_collection = None
_reranker: Optional[CrossEncoder] = None
_reranker_unavailable = False

_sources_cache = database.TTLCache(config.FILENAME_CACHE_TTL_SECONDS)
_bm25_index_cache: dict = {}

# ChromaDB's embedded PersistentClient spins up internal background
# threads (its pubsub/telemetry subsystem) tied to whatever OS thread
# creates and first uses it. FastAPI's sync `def` endpoints run on a
# rotating threadpool (a different worker thread per request, not
# guaranteed to be the same one) -- confirmed live: a question that always
# succeeded via a plain single-threaded script failed 100% of the time
# through the API with a bare `KeyError` ("Label not found" spam from the
# client's internal consumer threads misrouting). Funneling every
# Chroma-touching call through one dedicated, always-the-same thread
# fixes this at the root instead of retrying around it.
_chroma_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chroma-io")


def run_on_chroma_thread(fn, *args, **kwargs):
    return _chroma_thread.submit(fn, *args, **kwargs).result()


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(config.EMBEDDING_MODEL)
    return _embedder


def get_reranker() -> Optional[CrossEncoder]:
    """Lazily loads the cross-encoder. On this 8GB-RAM laptop, if it can't
    load (OOM, download failure), retrieval must still work -- degrade to
    embedding-only ranking rather than fail the request."""
    global _reranker, _reranker_unavailable
    if _reranker_unavailable:
        return None
    if _reranker is None:
        try:
            _reranker = CrossEncoder(config.RERANK_MODEL)
        except Exception:
            _reranker_unavailable = True
            return None
    return _reranker


def rerank(question: str, hits: list[dict], top_k: int) -> list[dict]:
    """Cross-encoder reranking of the wider embedding-candidate set fetched
    by retrieve(). An embedding comparing the question and a chunk
    independently often can't tell two near-identical boilerplate clauses
    apart; a cross-encoder scores the (question, chunk) pair jointly and
    is measurably better at picking the one that's actually on-topic (see
    the mmarco-mMiniLMv2 A/B check: an off-topic clause that tied for 2nd
    place under pure embedding similarity got correctly demoted here)."""
    if not hits:
        return hits
    reranker = get_reranker()
    if reranker is None:
        return hits[:top_k]
    pairs = [(question, h["text"]) for h in hits]
    scores = reranker.predict(pairs)
    for h, s in zip(hits, scores):
        h["rerank_score"] = float(s)
    ranked = sorted(hits, key=lambda h: h["rerank_score"], reverse=True)
    return ranked[:top_k]


def get_collection():
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        _collection = _chroma_client.get_or_create_collection(
            name=_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
    return _collection


def list_indexed_sources() -> list[str]:
    """Short-TTL cached listing -- avoids recomputing per-call within one
    request (known-bug: called 3x per question added seconds of overhead)."""
    return _sources_cache.get_or_set(database.list_document_sources)


def invalidate_sources_cache() -> None:
    _sources_cache.invalidate()
    _bm25_index_cache.clear()  # BM25 indexes are stale the moment the corpus changes


# ---------------------------------------------------------------------------
# Named / shorthand document targeting
# ---------------------------------------------------------------------------

_CONNECTOR = r"(?:(?:no\.?|nomor|number)\s+)?"

# Contract-type words ("Konsultasi", "Jasa", ...) sitting between "kontrak"
# and the number ("kontrak konsultasi 104") broke the shorthand match the
# same way "kontrak nomor 106" did before it -- same root cause, a rigid
# `noun <number>` pattern with no tolerance for a word in between. Rather
# than hardcode this corpus's five type words (which won't necessarily
# match the real STK naming convention once ported), the vocabulary is
# parsed from the filenames actually indexed right now, the same principle
# `heading_lead_words()` uses for identifier grounding: derive the
# recognized words from real content instead of a fixed list.
_CONTRACT_FILENAME_TYPE_RE = re.compile(r"^kontrak[_\s]+([a-zA-Z]+)[_\s]+\d", re.IGNORECASE)


def _contract_type_words(sources: list[str]) -> set[str]:
    words = set()
    for source in sources:
        m = _CONTRACT_FILENAME_TYPE_RE.match(source)
        if m:
            words.add(m.group(1).lower())
    return words


def _build_shorthand_patterns(sources: list[str]) -> list[re.Pattern]:
    type_words = _contract_type_words(sources)
    type_group = ""
    if type_words:
        type_group = r"(?:(?:" + "|".join(re.escape(w) for w in sorted(type_words)) + r")\s+)?"
    # tolerates, in any combination: a bare number ("kontrak 104"), a
    # connector word ("kontrak nomor 104"), and/or a real contract-type
    # word pulled from the indexed corpus ("kontrak konsultasi 104",
    # "kontrak konsultasi nomor 104") -- instead of requiring the number to
    # immediately follow the noun.
    return [
        re.compile(r"\b(contract|kontrak)\s+" + type_group + _CONNECTOR + r"(\d+)", re.IGNORECASE),
        re.compile(r"\b(sop)\s+" + _CONNECTOR + r"(\d+)", re.IGNORECASE),
    ]


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_for_fuzzy_match(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", text.lower()).strip()


def named_target_sources(question: str) -> list[str]:
    """Returns real indexed filenames referenced in the question, in the
    order the reference appears in the question text (first-named noun
    wins -- see known-bug #7 for why unordered matching is unsafe)."""
    sources = list_indexed_sources()
    matches: list[tuple[int, str]] = []  # (position, source)

    # 1. exact filename mention (with or without .pdf)
    for source in sources:
        stem = source[:-4] if source.lower().endswith(".pdf") else source
        for needle in (source, stem):
            pos = question.lower().find(needle.lower())
            if pos != -1:
                matches.append((pos, source))
                break

    # 2. shorthand mentions: "contract 250", "kontrak 250", "sop 45",
    # "kontrak konsultasi 104", "kontrak nomor 106", etc.
    for pattern in _build_shorthand_patterns(sources):
        for m in pattern.finditer(question):
            number = m.group(2)
            candidates = [s for s in sources if number in s]
            for c in candidates:
                matches.append((m.start(), c))

    # 3. fuzzy filename mention: real filenames are long, punctuation-heavy
    # codes ("1._A-017-DSI3000_2024_S9_Rev_00_...pdf") that no one types
    # verbatim from memory -- typing the same words with different
    # underscores/spaces/brackets shouldn't fall through to a fuzzy,
    # corpus-wide search when the user was clearly naming one specific
    # document (confirmed live: a near-exact retyping of a real filename,
    # differing only in separators, missed the exact-match check above and
    # fell through to multi-source discovery, which then pulled in other
    # documents that merely CITE this one in a references list -- a
    # precision problem caused by never recognizing the document was
    # actually named). Comparing on a punctuation-stripped normal form
    # catches this without any per-corpus vocabulary. Only applied to
    # filenames with enough real content (not just a bare number) to make
    # a coincidental match implausible.
    if not matches:
        norm_question = _normalize_for_fuzzy_match(question)
        for source in sources:
            stem = source[:-4] if source.lower().endswith(".pdf") else source
            norm_stem = _normalize_for_fuzzy_match(stem)
            if len(norm_stem) >= 20 and norm_stem in norm_question:
                matches.append((norm_question.find(norm_stem), source))

    if not matches:
        return []

    matches.sort(key=lambda t: t[0])
    seen = set()
    ordered: list[str] = []
    for _, src in matches:
        if src not in seen:
            seen.add(src)
            ordered.append(src)
    return ordered


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

_IDENTIFIER_EXTRACT_RE = re.compile(
    # any capitalized word (or hyphen/space-joined run of them) followed by
    # an alphanumeric identifier -- no fixed keyword list, so this covers
    # "Ayat 11.1", "Pasal 11", "Lampiran E1", "Tanggung Jawab - Sub 4",
    # "Referensi - Sub 9", or whatever the next document type phrases its
    # identifiers as, the same way heading_lead_words() in generation.py
    # builds its vocabulary from real content instead of a hardcoded list.
    # [ \t] (not \s): a real identifier is always written on one line, so
    # the separator must not also match a newline -- see generation.py's
    # _IDENTIFIER_PATTERN for the confirmed-live bug this mirrors.
    r"\b([A-Z][a-zA-Z]*(?:[ \t-]+[A-Z][a-zA-Z]*)*)[ \t]+([A-Za-z]?\d+(?:[.\-]\d+)?)\b"
)


def _exact_identifier_boost(question: str, source_filter: Optional[list[str]]) -> Optional[dict]:
    """Pure embedding search doesn't key strongly on a literal clause number
    (e.g. "Ayat 11.1", or a SOP's "Tanggung Jawab - Sub 4") when the document
    is full of near-identical boilerplate clauses -- confirmed on the
    regenerated, denser corpus, where the target clause sometimes fell
    outside top-k. When the question names a specific document AND a
    specific identifier, score each of that document's chunks by how much
    of the identifier phrase it actually contains and let the best one win
    outright -- an exact "phrase identifier" substring (contract-style)
    wins immediately; a compound reference whose wording doesn't appear
    verbatim in the source text (the SOP's "Sub-bagian N" body phrasing vs.
    a question asking about "Sub N") still wins by matching most of the
    phrase's distinguishing words plus the identifier itself, so the fix
    doesn't depend on the document's exact surface wording."""
    if not source_filter:
        return None
    match = _IDENTIFIER_EXTRACT_RE.search(question)
    if not match:
        return None
    phrase, ident = match.group(1), match.group(2)
    phrase_words = [w for w in re.split(r"[\s-]+", phrase) if w]
    if not phrase_words:
        return None
    ident_re = re.compile(r"\b" + re.escape(ident.lower()) + r"\b")
    exact_needle = f"{phrase} {ident}".lower()
    min_matched = max(1, len(phrase_words) - 1)  # tolerate one non-matching word (e.g. a connector)

    collection = get_collection()
    where = {"source": {"$in": source_filter}} if len(source_filter) > 1 else {"source": source_filter[0]}
    all_chunks = collection.get(where=where, include=["documents", "metadatas"])

    best_score = None
    best = None
    for doc, meta in zip(all_chunks.get("documents", []), all_chunks.get("metadatas", [])):
        doc_lower = doc.lower()
        if not ident_re.search(doc_lower):
            continue
        matched = sum(1 for w in phrase_words if w.lower() in doc_lower)
        if matched < min_matched:
            continue
        exact = exact_needle in doc_lower
        score = (exact, matched, -len(doc))
        if best_score is None or score > best_score:
            best_score = score
            best = (doc, meta)

    if best is None:
        return None
    doc, meta = best
    return {
        "text": doc,
        "source": meta.get("source"),
        "page": meta.get("page"),
        "distance": 0.0,
        "score": 1.0,
        "ocr_confidence": _meta_ocr_confidence(meta),
    }


def _meta_ocr_confidence(meta: dict) -> Optional[float]:
    val = meta.get("ocr_confidence")
    return None if val is None or val < 0 else val


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _bm25_cache_key(source_filter: Optional[list[str]]):
    return tuple(sorted(source_filter)) if source_filter else None


def _get_bm25_index(source_filter: Optional[list[str]]):
    """One BM25 index per distinct source_filter scope, invalidated whenever
    the corpus changes (see invalidate_sources_cache()). Corpus is small
    enough that building an index per distinct scope on demand is cheap;
    no need for incremental updates."""
    key = _bm25_cache_key(source_filter)
    if key in _bm25_index_cache:
        return _bm25_index_cache[key]

    collection = get_collection()
    where = None
    if source_filter:
        where = {"source": {"$in": source_filter}} if len(source_filter) > 1 else {"source": source_filter[0]}
    fetched = collection.get(where=where, include=["documents", "metadatas"])
    ids = fetched.get("ids", [])
    docs = fetched.get("documents", [])
    metas = fetched.get("metadatas", [])

    entry = None if not docs else (BM25Okapi([_tokenize(d) for d in docs]), ids, docs, metas)
    _bm25_index_cache[key] = entry
    return entry


def _bm25_search(question: str, source_filter: Optional[list[str]], k: int) -> list[dict]:
    """Sparse/keyword retrieval pass alongside the embedding pass. On short,
    structurally-repetitive legal text, an embedding can rank the chunk with
    the exact WRONG Pasal number as more similar than the chunk with the
    right one -- every clause uses near-identical wording, so the literal
    number is often the only real signal. BM25 catches that literal match
    directly; results get combined with the embedding ranking via
    reciprocal rank fusion in retrieve() rather than trusted alone."""
    entry = _get_bm25_index(source_filter)
    if entry is None:
        return []
    bm25, ids, docs, metas = entry
    scores = bm25.get_scores(_tokenize(question))
    ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [
        {"id": ids[i], "text": docs[i], "meta": metas[i]}
        for i in ranked_idx
        if scores[i] > 0
    ]


def _cosine_sim(a, b) -> float:
    a, b = np.array(a), np.array(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


def retrieve(question: str, source_filter: Optional[list[str]] = None, k: int = config.TOP_K) -> list[dict]:
    collection = get_collection()
    embedder = get_embedder()
    query_embedding = embedder.encode([question], show_progress_bar=False).tolist()

    where = None
    if source_filter:
        where = {"source": {"$in": source_filter}} if len(source_filter) > 1 else {"source": source_filter[0]}

    candidate_k = max(k, config.RERANK_CANDIDATE_K) if config.RERANK_ENABLED else k
    result = collection.query(
        query_embeddings=query_embedding,
        n_results=candidate_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    embedding_ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]

    hits_by_id: dict[str, dict] = {}
    embedding_rank: dict[str, int] = {}
    for rank, (hid, doc, meta, dist) in enumerate(zip(embedding_ids, docs, metas, dists)):
        embedding_rank[hid] = rank
        hits_by_id[hid] = {
            "text": doc,
            "source": meta.get("source"),
            "page": meta.get("page"),
            "distance": dist,
            "score": 1 - dist,  # cosine distance -> similarity-ish score; preserved as-is
            # for anything comparing scores across hits (e.g. ambiguity
            # detection's score-gap check) -- RRF fusion below only decides
            # which candidates advance to reranking, it never overwrites
            # this field's meaning.
            "ocr_confidence": _meta_ocr_confidence(meta),
        }

    bm25_rank: dict[str, int] = {}
    if config.HYBRID_SEARCH_ENABLED:
        bm25_hits = _bm25_search(question, source_filter, config.BM25_CANDIDATE_K)
        bm25_only_texts, bm25_only_ids = [], []
        for rank, h in enumerate(bm25_hits):
            bm25_rank[h["id"]] = rank
            if h["id"] not in hits_by_id:
                bm25_only_ids.append(h["id"])
                bm25_only_texts.append(h["text"])
                hits_by_id[h["id"]] = {
                    "text": h["text"],
                    "source": h["meta"].get("source"),
                    "page": h["meta"].get("page"),
                    "distance": None,
                    "score": 0.0,  # placeholder, replaced below once embedded
                    "ocr_confidence": _meta_ocr_confidence(h["meta"]),
                }
        # a BM25-only hit has no cosine score yet -- compute one so it's
        # comparable to embedding-sourced hits wherever "score" is read
        # downstream (ambiguity detection, avg-confidence logging), instead
        # of leaving a placeholder that would always look like the weakest
        # possible match.
        if bm25_only_texts:
            embeddings = embedder.encode(bm25_only_texts, show_progress_bar=False)
            for hid, emb in zip(bm25_only_ids, embeddings):
                sim = _cosine_sim(query_embedding[0], emb)
                hits_by_id[hid]["score"] = sim
                hits_by_id[hid]["distance"] = 1 - sim

    if bm25_rank:
        rrf_scores = {
            hid: 1.0 / (config.RRF_K + embedding_rank.get(hid, 10**6) + 1)
            + 1.0 / (config.RRF_K + bm25_rank.get(hid, 10**6) + 1)
            for hid in hits_by_id
        }
        ordered_ids = sorted(hits_by_id.keys(), key=lambda hid: rrf_scores[hid], reverse=True)
    else:
        ordered_ids = sorted(hits_by_id.keys(), key=lambda hid: embedding_rank.get(hid, 10**6))

    hits = [hits_by_id[hid] for hid in ordered_ids[:candidate_k]]

    if config.RERANK_ENABLED:
        hits = rerank(question, hits, k)
    else:
        hits = hits[:k]

    boost = _exact_identifier_boost(question, source_filter)
    if boost and not any(h["text"] == boost["text"] for h in hits):
        boost.setdefault("ocr_confidence", None)
        hits = [boost] + hits[: k - 1]

    return hits


# ---------------------------------------------------------------------------
# Ambiguity detection
# ---------------------------------------------------------------------------

_GENERIC_IDENTIFIER_PATTERN = re.compile(
    r"\b(pasal|lampiran|bab)\s+([A-Za-z]?\d+)", re.IGNORECASE
)


class AmbiguityResult:
    def __init__(self, candidates: list[str]):
        self.candidates = candidates


def detect_ambiguity(question: str, hits: list[dict], named_sources: list[str]) -> Optional[AmbiguityResult]:
    """Bare generic identifiers (e.g. "Pasal 8") with no named document and
    multiple close-scoring distinct sources should trigger clarification
    instead of silently picking one. Bypassed whenever a document is
    actually named in the question."""
    if named_sources:
        return None
    if not _GENERIC_IDENTIFIER_PATTERN.search(question):
        return None

    # collect best score per distinct source
    best_per_source: dict[str, float] = {}
    for h in hits:
        src = h["source"]
        if src not in best_per_source or h["score"] > best_per_source[src]:
            best_per_source[src] = h["score"]

    if len(best_per_source) < 2:
        return None

    ranked = sorted(best_per_source.items(), key=lambda t: t[1], reverse=True)
    top_score = ranked[0][1]
    second_score = ranked[1][1]
    if (top_score - second_score) <= config.AMBIGUITY_SCORE_GAP:
        candidates = [src for src, score in ranked if (top_score - score) <= config.AMBIGUITY_SCORE_GAP]
        return AmbiguityResult(candidates=candidates)
    return None


# ---------------------------------------------------------------------------
# Scoped heading lookups
# ---------------------------------------------------------------------------

def get_headings_for(sources: list[str]) -> list[dict]:
    """Never call database.get_all_headings_grouped_by_source() for a
    per-question lookup -- always scope to the relevant document(s)
    (known-bug #2)."""
    return database.get_headings_for_sources(sources)


def get_full_document_text(source: str) -> str:
    """Reconstructs (approximately) the full document from its stored
    chunks, ordered by (page, chunk_index) -- used by the full-document
    fallback for small documents. Not a byte-perfect reconstruction (the
    sliding-window fallback in ingestion.py's chunker can leave a little
    overlap duplication), but that's harmless padding for a context-fit
    check and a one-shot full-text prompt, not something read as ground
    truth elsewhere."""
    collection = get_collection()
    fetched = collection.get(where={"source": source}, include=["documents", "metadatas"])
    docs = fetched.get("documents", [])
    metas = fetched.get("metadatas", [])
    ordered = sorted(
        zip(metas, docs), key=lambda pair: (pair[0].get("page", 0), pair[0].get("chunk_index", 0))
    )
    return "\n\n".join(doc for _, doc in ordered)


# ---------------------------------------------------------------------------
# Enumerate-intent routing
# ---------------------------------------------------------------------------
#
# "list all Pasal in kontrak 102" answered through normal similarity
# retrieval returned only 4 of the real ~25 -- top-k similarity search was
# never designed to guarantee completeness, it returns the *most relevant*
# chunks, not *all* of them. "Sebutkan semua"/"list all" phrasing already
# routed around retrieval via broad_category_listing() below, but a
# differently-phrased version of the same request ("apa saja pasal yang
# ada di kontrak 102") used none of the recognized trigger words and fell
# straight through to the incomplete top-k path -- confirmed live: the
# exact same question worded as "list all Pasal..." returned all 22 real
# headings, while "apa saja pasal..." returned None from this function and
# silently fell back to a 4-of-22 answer. Expanded triggers below close
# that specific gap; the deeper fix is that ANY of these trigger phrases
# combined with a real structural-element word must short-circuit before
# retrieval ever runs, which generate_answer() already does by checking
# this function first.

_ENUMERATE_TRIGGERS = (
    "list all", "daftar", "sebutkan semua", "apa saja", "berapa banyak", "semua", "ada berapa",
)


def _category_candidates_from_headings(headings: list[dict]) -> set[str]:
    """Category words ("Pasal", "Lampiran", ...) are pulled from the real
    headings actually stored for the document(s) in play, not a fixed list
    -- same principle as the identifier-boost and shorthand-matching
    vocabularies elsewhere in this module. A heading's first word, if
    alphabetic, is a category-word candidate; bare numeric headings (a
    contract's isolated ayat marker line, e.g. "11.1") contribute nothing
    here, which is correct -- "Ayat" isn't a listable category the way
    "Pasal"/"Lampiran" are, it's a sub-item within one."""
    words = set()
    for h in headings:
        first = h["text"].strip().split()[0] if h["text"].strip() else ""
        first = re.sub(r"[^A-Za-z]", "", first).lower()
        if first:
            words.add(first)
    return words


def resolve_broad_category(question: str, candidates: set[str]) -> Optional[str]:
    """Pick the category word by the order it appears in the question, not
    unordered set iteration (known-bug #7)."""
    lower = question.lower()
    best_pos = None
    best_word = None
    for word in candidates:
        pos = lower.find(word)
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos = pos
            best_word = word
    return best_word


def _natural_sort_key(text: str):
    """Sorts "Pasal 2" before "Pasal 10" -- plain lexicographic sort put
    "Pasal 10" before "Pasal 2", which reads as broken/out-of-order to
    anyone who knows the document."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def broad_category_listing(question: str, named_sources: list[str]) -> Optional[dict]:
    """For questions like "list all lampiran/pasal sections" or "apa saja
    pasal yang ada". Resolves the category from real stored headings,
    requires >=2 distinct heading texts to qualify (filters out
    boilerplate/running-header artifacts), and formats the answer directly
    from headings with zero LLM call -- so the count can't be dropped by
    retrieval depth or hallucinated by the model."""
    is_enumerate = any(w in question.lower() for w in _ENUMERATE_TRIGGERS)
    if not is_enumerate:
        return None

    sources = named_sources or list_indexed_sources()
    headings = get_headings_for(sources)

    category = resolve_broad_category(question, _category_candidates_from_headings(headings))
    if not category:
        return None

    matching = [h for h in headings if h["text"].lower().startswith(category)]
    distinct_texts = sorted(set(h["text"] for h in matching), key=_natural_sort_key)

    if len(distinct_texts) < config.HEADING_MIN_CATEGORY_HEADINGS:
        return None

    return {"category": category, "headings": distinct_texts, "sources": sources}


# ---------------------------------------------------------------------------
# Document-type-level enumerate ("berapa banyak SOP yang ada", "sebutkan
# semua kontrak yang ada") -- a different kind of completeness question:
# not "list every heading of type X within a document", but "list/count
# every indexed FILE of type X". Handled separately from
# broad_category_listing() because the unit being counted is a document,
# not a heading.
# ---------------------------------------------------------------------------

def document_type_listing(question: str, named_sources: list[str]) -> Optional[dict]:
    # a specific document was already named ("kontrak 102") -- that means
    # the question is about content *within* that one document, not a
    # count/list of all documents of a type, even though "kontrak" appears
    # in both phrasings.
    if named_sources:
        return None
    is_enumerate = any(w in question.lower() for w in _ENUMERATE_TRIGGERS)
    if not is_enumerate:
        return None

    lower = question.lower()
    doc_type = None
    best_pos = None
    for word, pos in (("sop", lower.find("sop")), ("kontrak", lower.find("kontrak"))):
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos = pos
            doc_type = word
    if doc_type is None:
        return None

    sources = sorted(
        (s for s in list_indexed_sources() if s.lower().startswith(doc_type)),
        key=_natural_sort_key,
    )
    if not sources:
        return None

    return {"doc_type": doc_type, "sources": sources}
