# STK Online RAG — Temporary Laptop Build

Local rebuild of the STK Online RAG system for continued development while
the main development laptop (HP 15 Gaming dk1064-TX) is in for repair. This
is a functional-testing setup on weaker hardware (8GB RAM, MX330 2GB VRAM),
not a reproduction of the full 600-file benchmark. It has grown well beyond
the original small rebuild: reranking, OCR, structure-aware chunking, a
document preview UI, real chat sessions, and a dark theme all landed here
in later passes. This file describes what's actually in the system now.

## Model choice

The main-machine build uses `aisingapore/Llama-SEA-LION-v2-8B-IT`. That
does not fit comfortably in 8GB RAM alongside Ollama + ChromaDB + FastAPI +
Streamlit + the embedding model + the reranker. This build uses
`qwen2.5:3b-instruct-q4_K_M`, which has reasonable Indonesian support and
fits in 8GB RAM. Expect weaker performance on Indonesian legal/technical
phrasing than the 8B SEA-LION — an accepted tradeoff for this temporary
setup, not something to fully compensate for in prompting.

## Setup

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Torch pulls a large default wheel with bundled CUDA on Windows; on a slow
connection, install the CPU-only wheel first (`pip install torch
--index-url https://download.pytorch.org/whl/cpu`), then run the full
`pip install -r requirements.txt` — it will see torch already satisfied.

External binaries needed beyond pip packages:

- **Ollama** (`winget install UB-Mannheim... ` no — `winget install
  Ollama.Ollama`), then `ollama pull qwen2.5:3b-instruct-q4_K_M`.
- **Poppler** (`winget install oschwartz10612.Poppler`) — required by
  `pdf2image` for PDF-to-image rendering, used both by OCR ingestion and
  the `/documents/{filename}/preview` thumbnail endpoint.
- **Tesseract OCR + Indonesian language pack** (`winget install
  UB-Mannheim.TesseractOCR`), then download `ind.traineddata` from
  `tesseract-ocr/tessdata_fast` into a tessdata directory and point
  `TESSDATA_PREFIX` at it (Program Files may need admin rights to write
  directly into its own tessdata folder — a user-writable copy works fine).

Generate the small synthetic test corpus (10 contracts + 8 SOPs, per
Section 10 of the rebuild spec — file count kept the same across later
passes, only per-document length and scanned-page ratio grew):

```bash
.venv\Scripts\python.exe -m app.corpus_generator.generate
```

Run the API (this also starts the APScheduler sync job and ingests on
first `/sync` call):

```bash
.venv\Scripts\uvicorn.exe app.api:app --host 0.0.0.0 --port 8000
```

Trigger the first ingestion:

```bash
curl -X POST http://localhost:8000/sync
```

Run the Streamlit frontend (separate terminal):

```bash
.venv\Scripts\streamlit.exe run streamlit_app.py
```

Run the scaled-down grounded evaluation:

```bash
.venv\Scripts\python.exe -m app.evaluate_grounded
```

## What's in the system

- [app/retrieval.py](app/retrieval.py) — `source_filter`-aware `retrieve()`;
  a wider embedding candidate set reranked by a cross-encoder
  (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) before the top-k is passed
  on; an exact-identifier boost that overrides both when the question names
  a specific clause/section number (dynamically built from real indexed
  filenames and retrieved chunk text, not a hardcoded vocabulary); ordered
  `named_target_sources()` with connector- and contract-type-tolerant
  shorthand matching ("kontrak 106", "kontrak nomor 106", "kontrak
  konsultasi 104" all resolve); ambiguity detection scoped to bare generic
  identifiers; scoped (never corpus-wide) heading lookups; broad-category
  deterministic listing.
- [app/ingestion.py](app/ingestion.py) — heading detection with the 5-line
  merge cap and the bold-sentence exclusion; OCR fallback for scanned pages
  with grayscale + Otsu binarization + deskew preprocessing and per-page
  confidence capture; structure-aware chunking that splits on detected
  headings first, falling back to clause/step-marker splitting and then a
  sliding window for content with no clean structure.
- [app/generation.py](app/generation.py) — conflicting-value attribution in
  the prompt; identifier grounding with a dynamic per-call vocabulary
  (keyed on the identifier's number/code, since a contract's ayat marker
  heading is stored bare with no leading word) and a value-vs-identifier
  filter (a number followed by a unit word like "juta"/"persen" is a value,
  not a section reference); `ClarificationNeeded` response shape;
  multi-question splitting that catches a conjunction-joined compound
  question even with only one trailing "?", and never silently drops a
  sub-question; bilingual (ID + EN) refusal/hedging detection; OCR-
  confidence and fabrication disclaimers surfaced directly in the answer
  text, not just logged.
- [app/sync_documents.py](app/sync_documents.py) — one reconciliation
  function shared by the APScheduler job (runs on `SYNC_INTERVAL_MINUTES`),
  the `/sync` endpoint, and the Streamlit sidebar button.
- [app/database.py](app/database.py) — paginated reads, short-TTL cache for
  the indexed-filenames listing, chat/session storage, per-message token
  counts, and the answer-flagging log (`human_flag`/`flag_category`/
  `corrected_answer`, exportable via `/export_flags`).
- [app/api.py](app/api.py) — `/ask`, `/sync`, `/sources`; `/documents`,
  `/documents/{filename}/preview` (thumbnail + text snippet), and
  `/documents/{filename}/download` (path-validated against the indexed
  sources list, not a raw filesystem lookup); `/sessions` (list/create),
  `/sessions/{id}/history`; `/flag/{log_id}`, `/export_flags`.
- [streamlit_app.py](streamlit_app.py) — dark-themed (`.streamlit/config.toml`
  is the single source of truth for color; kept custom CSS to spacing/
  borders/badges only, all colors chosen against the theme's own palette)
  UI with a sidebar document library grouped by Kontrak/SOP with preview
  cards, a real switchable session list (auto-titled from each session's
  first question, most-recent-first, with a per-session token-usage
  total), a "New chat" button, and the main chat + document-preview panel.

## Known limitations carried over (see rebuild spec Section 8)

- Non-bold, running-header-style headings (neither bold nor first-line-of-page)
  are not detected. No blanket "short line = heading" rule was added, since
  that floods results with false positives on documents with many short
  labeled fields. The inverse also happens occasionally: a short body
  sentence that happens to be a page's first line can get misdetected as a
  heading, adding noise to broad-category listings.
- Flat top-k embedding search still loses to densely-padded, near-identical
  boilerplate before reranking is applied; reranking and the exact-identifier
  boost close most of that gap for named-document questions, but a bare
  generic-identifier question with no named document still relies on
  embedding + rerank alone.
- No reliable automated fabrication detector when no real answer exists in
  context. Mitigated with a visible disclaimer plus the human-flagging
  system (`/flag/{log_id}`, `/export_flags`), not prevented outright. The
  flag/export endpoints are reachable and tested via direct API calls but
  not yet wired into a UI control (no "flag this answer" button in
  Streamlit).
- `_GENERIC_IDENTIFIER_PATTERN` (ambiguity detection) and `_CATEGORY_WORDS`
  (broad-category listing) in `retrieval.py` are still a fixed
  `(pasal|lampiran|bab)` list, unlike the exact-identifier boost and the
  shorthand-matching regex, which were generalized to derive their
  vocabulary from real indexed content. Same latent risk if ported to a
  document type using different words for these two features specifically;
  not yet hit by a real test case.
- The cross-encoder reranker (~470MB) adds real memory pressure on this
  8GB machine on top of the embedder, Ollama, and ChromaDB; it has worked
  in testing but with little headroom, and is worth watching for OOM-style
  flakiness under heavier load.
