"""Central configuration for STK Online RAG.

This is the temporary laptop profile (8GB RAM, MX330 2GB VRAM): a smaller
LLM and reduced retrieval depth compared to the main-machine build, per the
hardware section of the rebuild spec.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CORPUS_DIR = DATA_DIR / "corpus"
CHROMA_DIR = DATA_DIR / "chroma"
SQLITE_DIR = DATA_DIR / "sqlite"
SQLITE_PATH = SQLITE_DIR / "stk_online.db"
SESSION_LOGS_DIR = DATA_DIR / "session_logs"

for _d in (DATA_DIR, CORPUS_DIR, CHROMA_DIR, SQLITE_DIR, SESSION_LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Embeddings
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Local LLM via Ollama.
# Main-machine profile uses aisingapore/Llama-SEA-LION-v2-8B-IT.
# On this 8GB-RAM / 2GB-VRAM laptop that model doesn't fit alongside
# Chroma/FastAPI/Streamlit/embeddings running concurrently, so we use a
# smaller 3-4B class quantized instruct model instead (see README for the
# SEA-LION-smaller-variant check that was done before falling back to this).
OLLAMA_MODEL = "qwen2.5:3b-instruct-q4_K_M"
# Literal 127.0.0.1, not "localhost" -- on this machine "localhost" resolves
# IPv6 (::1) first, Ollama isn't reachable there, and every request pays a
# ~2 second connect-timeout-then-fallback-to-IPv4 tax before actually
# starting (confirmed live: 2040ms vs 4ms for the identical request).
OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT_SECONDS = 180

# Retrieval
# Raised from 4 -- confirmed live that a correct-but-lower-scoring chunk
# (the real document's own answer, buried among several superficially
# similar boilerplate-heavy chunks from the same document) sometimes
# didn't make a top-4 cut even though it was well within the top-25
# candidates the reranker already considers. Still well short of the
# main-machine default; this is a modest widening, not the full value.
TOP_K = 6
CHUNK_SIZE_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150

# Reranking: retrieve a wider embedding candidate set, then rerank with a
# cross-encoder that scores (question, chunk) jointly -- helps specifically
# when several near-identical boilerplate clauses compete for the same slot.
RERANK_ENABLED = True
RERANK_CANDIDATE_K = 25
RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # ~470MB, multilingual incl. Indonesian, CPU-friendly

# Hybrid search: pure embedding similarity can rank a chunk containing the
# exact WRONG Pasal number as more similar than the chunk with the right
# one, on short structurally-repetitive legal text where every clause uses
# near-identical wording. A BM25 (sparse/keyword) pass catches the literal
# identifier match embeddings can miss; both rank lists are combined via
# reciprocal rank fusion before reranking.
HYBRID_SEARCH_ENABLED = True
BM25_CANDIDATE_K = 25
RRF_K = 60  # standard RRF damping constant (Cormack et al.)

# Full-document fallback: for "enumerate" or small-document "broad"
# questions, skip retrieval entirely and inject the whole document when it
# clearly fits the model's context window, guaranteeing completeness
# regardless of chunk-level retrieval quality. 4096 is the context length
# Ollama actually runs this model at by default (`ollama ps` reports this
# at runtime), not the qwen2.5 architecture's own 32K max -- using the
# larger number here would silently overflow the real running context.
OLLAMA_CONTEXT_TOKENS = 4096
FULL_DOC_PROMPT_MARGIN_TOKENS = 1200  # system prompt + question + generation headroom
CHARS_PER_TOKEN_ESTIMATE = 4  # rough heuristic, no tokenizer call needed for a fast fit-check

# Ambiguity detection: if the top-2 distinct-source retrieval scores are
# within this gap, and no document was explicitly named, ask for clarification.
AMBIGUITY_SCORE_GAP = 0.03

# Multi-source discovery: when no document is named in the question, look
# at how many DISTINCT documents actually compete for the top retrieval
# slots -- not an absolute relevance-score cutoff (the cross-encoder's raw
# scores aren't calibrated to a known "this counts as relevant" threshold),
# just how many different documents show up among the best matches.
# <= MULTI_SOURCE_MAX_FILES distinct sources -> answer each in full,
# labeled by file. More than that -> too many to write out in full, so
# return a short labeled candidate list instead and let the next message
# (naming one of them) get the full answer, scoped to just that file.
MULTI_SOURCE_CANDIDATE_K = 10
MULTI_SOURCE_MAX_FILES = 3
MULTI_SOURCE_CANDIDATE_LIST_MAX = 8

# Heading detection / merging
HEADING_MAX_MERGE_LINES = 5  # was 3; that silently dropped legitimate 4-line headings
HEADING_MERGE_EXCLUDE_MIN_WORDS = 4  # bold line with >=4 words ending in .!? is a sentence, not a heading fragment
HEADING_MIN_CATEGORY_HEADINGS = 2  # a broad-category word needs >=2 distinct headings to qualify

# Caching
FILENAME_CACHE_TTL_SECONDS = 10

# Sync
SYNC_INTERVAL_MINUTES = 15

# API
API_HOST = "0.0.0.0"
API_PORT = 8000

# Chroma "too many SQL variables" style limits: page reads in batches
CHROMA_PAGE_SIZE = 500
