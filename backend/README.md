# STK Online RAG — backend

The RAG engine: FastAPI backend, ingestion pipeline (PDF/OCR/VLM/chunking),
retrieval (embeddings + rerank + boosts), generation, and all persistence
(ChromaDB + sqlite). Frontend-agnostic — nothing in here imports or knows
about Streamlit; see `../frontend-streamlit/` for the current UI and
`../docs/ARCHITECTURE.md` for the full system design.

This file originally described a temporary 8GB-RAM/MX330-GPU laptop build;
the project has since migrated to current hardware (see `MIGRATION.md`)
and grown well beyond that initial scope. Current accurate model/hardware
config lives in `../docs/ARCHITECTURE.md` §6 — treat that as the source of
truth if anything below conflicts with it.

## Model choice

Current config (see `app/config.py`): `qwen3.5:9b` (LLM) and `qwen2.5vl:7b`
(VLM, diagram description) via local Ollama, on an i7-240H / RTX 5050 8GB
VRAM machine. Thinking mode is disabled on the LLM call (`"think": false`)
for latency — see `../docs/ARCHITECTURE.md` and `../docs/EVALUATION_REPORT.md`
for why and the measured impact.

An earlier pass of this project ran `qwen2.5:3b-instruct-q4_K_M` on a
temporary 8GB-RAM/MX330-2GB-VRAM laptop while the main machine was in for
repair — that constraint no longer applies, kept here only as history.

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

- **Ollama** (`winget install Ollama.Ollama`), then
  `ollama pull qwen3.5:9b` and `ollama pull qwen2.5vl:7b`.
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

Run the API from inside `backend/` (this also starts the APScheduler sync
job and ingests on first `/sync` call):

```bash
.venv\Scripts\uvicorn.exe app.api:app --host 0.0.0.0 --port 8000
```

Trigger the first ingestion:

```bash
curl -X POST http://localhost:8000/sync
```

Run the Streamlit frontend — **from inside `../frontend-streamlit/`**, not
here (the frontend was split into its own sibling folder, but still runs in
this same `.venv`/environment; it auto-launches this backend if it isn't
already running):

```bash
cd ..\frontend-streamlit
..\backend\.venv\Scripts\streamlit.exe run streamlit_app.py
```

Run the grounded evaluation (from inside `backend/`):

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
- [`../frontend-streamlit/streamlit_app.py`](../frontend-streamlit/streamlit_app.py)
  — dark-themed (`.streamlit/config.toml` is the single source of truth for
  color; kept custom CSS to spacing/borders only, all colors chosen against
  the theme's own palette) UI with a plain vertical sidebar document list
  (no type grouping — a compact scrollable list works better at 1,177+
  documents than grouped cards), a closable/reopenable preview panel, a
  real switchable session list (auto-titled from each session's first
  question, most-recent-first, with a per-session token-usage total), a
  "New chat" button, a human-flagging control under each answer (wired to
  `/flag/{log_id}`), and a fixed disclaimer under every AI answer.

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
  context. Mitigated with a visible disclaimer under every answer plus the
  human-flagging system (`/flag/{log_id}`, `/export_flags`), not prevented
  outright — a real flag control is wired into the Streamlit UI under each
  answer.
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
