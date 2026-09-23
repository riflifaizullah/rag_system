# Moving STK Online RAG to the new laptop (i7-240H, RTX 5050 8GB VRAM)

## What actually moved, and how

- **Code** — pushed to GitHub (private): `github.com/riflifaizullah/rag_system_stk`.
  Clone it on the new laptop and you have everything code-related.
- **Data** (`data/corpus/`, the vector index, session history) — deliberately
  **never went to GitHub**, even though the repo is private. `data/corpus/`
  and the vector index built from it both contain real internal company
  documents (signed contracts, an employee health-risk-assessment document
  with real names in it) — that doesn't leave your own machines. Get the
  PDFs onto the new laptop by whatever means is easiest for you (not
  prescribed here), then rebuild everything with one command (see below) —
  don't bother copying the old `data/chroma/`/`data/sqlite/`, since a fresh
  rebuild picks up this session's ingestion-pipeline fixes (column-aware
  OCR reconstruction, heading-detection cleanup) that the old data doesn't
  have.
- **This conversation's history** — local to the old laptop
  (`C:\Users\M. Rezha Faisal\.claude\`), not carried over automatically.
  Starting a fresh Claude Code session on the new laptop won't remember
  this conversation; this file (and the code comments throughout the
  project) are what actually carries the accumulated context forward.

## New laptop setup, in order

1. **Clone the repo**:
   ```
   git clone https://github.com/riflifaizullah/rag_system_stk.git
   cd rag_system_stk
   ```
2. **Python 3.11**, then the venv:
   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. **GPU-enabled torch** — step 2 installs the CPU-only build by default.
   Check your driver's max CUDA version first (`nvidia-smi`, top-right),
   then replace torch with a matching CUDA build (Blackwell/RTX 50-series
   needs a recent build, e.g. `cu128` if your driver supports it):
   ```
   pip uninstall torch torchvision torchaudio -y
   pip install torch --index-url https://download.pytorch.org/whl/cu128
   python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
   ```
   Should print `True` and your GPU's name. The embedder and reranker use
   CUDA automatically once this is true -- no code changes needed.
4. **Ollama**, then the model:
   ```
   ollama pull qwen2.5:7b-instruct
   ```
5. **Tesseract OCR** — install the Windows build, include the Indonesian
   (`ind`) language pack, set `TESSDATA_PREFIX` to wherever its `tessdata`
   folder ends up.
6. **Poppler** — install, make sure it's on PATH.
7. **Get the PDFs into `data/corpus/`** (auto-created the first time any
   app code runs, e.g. `python -c "from app import config"`), then build
   the index:
   ```
   python -m app.sync_documents
   ```
   For ~1000 real files this will take a long time, especially any scanned
   pages -- don't expect the few-minute turnaround this session's 20-file
   corpus had.
8. Run it: `uvicorn app.api:app --host 0.0.0.0 --port 8000` and
   `streamlit run streamlit_app.py`.

## What changed in `config.py` for this hardware (already committed)

| Setting | Old (temp laptop) | New | Why |
|---|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct-q4_K_M` | `qwen2.5:7b-instruct` | Same family already proven on this project's Indonesian content; bigger model fits comfortably now. (SEA-LION-8B was the *original* intended model per earlier comments in this file, but its Ollama availability is unverified -- worth trying as a follow-up experiment, not gambling on it during migration.) |
| `TOP_K` | 6 | 8 | More retrieval headroom with GPU + more RAM |
| `RERANK_CANDIDATE_K` / `BM25_CANDIDATE_K` | 25 | 35 | Same reason |
| `MULTI_SOURCE_CANDIDATE_K` | 10 | 15 | Same reason |
| `OLLAMA_CONTEXT_TOKENS` | 4096 (confirmed via `ollama ps` on the old laptop) | 8192 (**unverified guess**) | **Check this on the new machine** — run a real query, then `ollama ps`, and correct this value to match what Ollama actually reports. Too-high silently overflows the real context instead of falling back safely, which is exactly the failure mode this value exists to prevent. |

## Verifying the move worked

1. `GET /health` — confirms the API + Ollama model are up.
2. Ask something and check `/ask`'s `sources` field points at the right
   file.
3. `/monitor` shows both services online.
4. `ollama ps` after a real query — confirm `OLLAMA_CONTEXT_TOKENS` above
   matches reality, adjust if not.
