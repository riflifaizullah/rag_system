"""Central configuration for STK Online RAG.

Profile as of the migration to the new machine (i7-240H, RTX 5050 8GB VRAM)
-- raised from the original temporary-laptop profile (8GB RAM, MX330 2GB
VRAM) that every value here was tuned down for. sentence-transformers'
SentenceTransformer/CrossEncoder auto-detect CUDA with zero code changes;
they just need a CUDA-enabled torch build installed (the default pip
install pulls CPU-only -- see MIGRATION.md) to actually use the GPU.
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

# Local LLM via Ollama. Raised from qwen2.5:3b-instruct-q4_K_M (the
# temporary-laptop profile) now that there's a real GPU to run on -- picked
# qwen2.5:7b-instruct specifically to stay in the same model family that
# was already confirmed working well on this project's Indonesian content
# all through the temporary-laptop phase, rather than gambling on
# aisingapore/Llama-SEA-LION-v2-8B-IT's availability/behavior unverified.
# Worth trying SEA-LION as a follow-up experiment once migration itself is
# stable, not before.
OLLAMA_MODEL = "qwen2.5:7b-instruct"
# Literal 127.0.0.1, not "localhost" -- on this machine "localhost" resolves
# IPv6 (::1) first, Ollama isn't reachable there, and every request pays a
# ~2 second connect-timeout-then-fallback-to-IPv4 tax before actually
# starting (confirmed live: 2040ms vs 4ms for the identical request).
OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT_SECONDS = 180

# Retrieval
# Raised again for the new hardware (was 4 on the temporary laptop, then 6
# after confirming live that correct-but-lower-scoring chunks sometimes
# missed the cutoff). A real GPU + more RAM can afford considering more
# candidates without the latency cost this mattered for before.
TOP_K = 8
CHUNK_SIZE_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150

# Reranking: retrieve a wider embedding candidate set, then rerank with a
# cross-encoder that scores (question, chunk) jointly -- helps specifically
# when several near-identical boilerplate clauses compete for the same slot.
RERANK_ENABLED = True
RERANK_CANDIDATE_K = 35
RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # ~470MB, multilingual incl. Indonesian, now GPU-capable

# Hybrid search: pure embedding similarity can rank a chunk containing the
# exact WRONG Pasal number as more similar than the chunk with the right
# one, on short structurally-repetitive legal text where every clause uses
# near-identical wording. A BM25 (sparse/keyword) pass catches the literal
# identifier match embeddings can miss; both rank lists are combined via
# reciprocal rank fusion before reranking.
HYBRID_SEARCH_ENABLED = True
BM25_CANDIDATE_K = 35
RRF_K = 60  # standard RRF damping constant (Cormack et al.)

# Full-document fallback: for "enumerate" or small-document "broad"
# questions, skip retrieval entirely and inject the whole document when it
# clearly fits the model's context window, guaranteeing completeness
# regardless of chunk-level retrieval quality.
# IMPORTANT -- VERIFY THIS ON THE NEW MACHINE before trusting it: 4096 was
# confirmed via `ollama ps` as what Ollama actually ran qwen2.5:3b at on
# the old laptop, not assumed. This 8192 is a reasonable starting guess for
# qwen2.5:7b on real hardware, NOT yet confirmed the same way -- check
# `ollama ps` after a real query on the new machine and correct this value
# to match reality. Using a too-large number here silently overflows the
# real running context instead of falling back safely.
OLLAMA_CONTEXT_TOKENS = 8192
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
MULTI_SOURCE_CANDIDATE_K = 15
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
