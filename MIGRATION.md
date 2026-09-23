# Moving STK Online RAG to the new laptop

Two things move separately: the **code** (via git, already set up) and the
**data** (via your external HDD — too large/binary for git).

## 1. What's on git now

A local git repo was just initialized here with everything except `.venv/`
and `data/` (see `.gitignore`). On the new laptop, once you've copied this
folder over (or cloned/pulled it, if you push this repo somewhere), the
code itself is already there.

If you want a remote (GitHub, etc.) so you don't have to carry the code on
the HDD too, say so and I'll help set that up — didn't do it automatically
since it means creating something outside this machine.

## 2. What to copy via the external HDD

Copy the whole project folder **except `.venv/`** (it's not portable
between machines — recreate it fresh, see below). Total size right now://
~106 MB code+data, dominated by:

| Folder | Size | What it is | Skip it? |
|---|---|---|---|
| `data/corpus/` | 40 MB | Your 20 real source PDFs | No — original data |
| `data/chroma/` | 64 MB | Pre-built vector index (embeddings) | No — copying this saves you a full re-ingestion (OCR + embedding), which took 15-20+ minutes per run on this laptop |
| `data/sqlite/` | 2 MB | Session history, headings index, document registry | No |
| `.venv/` | large | Python virtual environment | **Yes, skip** — recreate on the new machine (see step 3) |

Practically: copy the entire `RAG` folder to the HDD, then on the new
laptop delete/skip `.venv/` before copying it in, or just don't copy that
one subfolder.

## 3. New laptop setup, in order

1. **Python 3.11** (matches what's used here — `python --version` should
   show 3.11.x). Install from python.org if not already present.
2. **Recreate the venv** in the copied project folder:
   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. **Ollama** — install from ollama.com, then pull the same model:
   ```
   ollama pull qwen2.5:3b-instruct-q4_K_M
   ```
   (See the hardware-upgrade section below before deciding whether to
   stick with this model on the new machine.)
4. **Tesseract OCR** (for scanned-page ingestion) — install the Windows
   build, include the Indonesian language pack (`ind`) during setup, and
   set `TESSDATA_PREFIX` to wherever its `tessdata` folder ends up (this
   laptop uses `%LOCALAPPDATA%\tessdata`).
5. **Poppler** (used by `pdf2image` for thumbnails/OCR page rendering) —
   install and make sure it's on PATH.
6. **Copy `data/`** from the HDD into the new project folder.
7. Run the app the same way as here: `uvicorn app.api:app --host 0.0.0.0
   --port 8000` and `streamlit run streamlit_app.py`.

## 4. Since the new laptop has a real GPU and 16GB+ RAM

Several settings in `app/config.py` were deliberately kept low specifically
*because* of this laptop's limits (8GB RAM, MX330 2GB VRAM) — the comments
in that file say so explicitly. Worth revisiting once you're confirmed
running on the new hardware, not before (don't change them here — this
laptop still needs to work in the meantime):

- **`TOP_K`, `RERANK_CANDIDATE_K`, `MULTI_SOURCE_CANDIDATE_K`** — currently
  6 / 25 / 10. A real GPU and more RAM can comfortably support higher
  values, which directly reduces the "correct chunk didn't make the
  top-k" problem we found and partly fixed this session.
- **The LLM itself** — `qwen2.5:3b-instruct-q4_K_M` was chosen specifically
  because nothing bigger fit here alongside everything else running. On
  real hardware, a meaningfully larger/less-quantized model (still via
  Ollama) would likely improve answer quality noticeably, especially on
  the more complex real STK documents.
- **GPU-accelerated embedding/reranking** — install a CUDA-enabled
  `torch` build (not the `+cpu` one this laptop has) so the
  sentence-transformers embedder and cross-encoder reranker actually run
  on the GPU instead of CPU. On *this* laptop this wasn't worth doing
  (2GB VRAM, already contended by Ollama) — that calculus changes with a
  real GPU.
- **`OLLAMA_CONTEXT_TOKENS`** — currently 4096, matching what Ollama
  actually ran at here. Worth re-checking (`ollama show <model>`) on the
  new machine; a bigger context window directly enables larger
  full-document-fallback answers.

I'd treat this as a deliberate follow-up once you're actually on the new
machine and can verify each change live, not something to guess at now.

## 5. Verifying the move worked

Once running on the new laptop:
1. `GET /health` — confirms the API + Ollama model are up.
2. Ask a question you already know the answer to from this session (e.g.
   "kapan dokumen A-017 mulai berlaku?" should answer "1 November 2024").
3. Check `/monitor` shows both services online.
4. Open Streamlit, confirm your session history (107+ sessions) carried
   over correctly from the copied `data/sqlite/`.
