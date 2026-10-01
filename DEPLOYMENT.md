# Deployment / running this on another machine

This covers the Blazor Server website (`dotnet/RagSystemWeb/`) and how it
relates to the Python backend. For installing the backend's own
dependencies from scratch (conda env, Ollama, Tesseract, Poppler, pulling
the corpus onto a new machine) see **`backend/MIGRATION.md`** first — this
file assumes that part is already done and focuses on what's specific to
running the website layer and the two-process relationship.

## Components and ports

| Component | Path | Port | Notes |
|---|---|---|---|
| Backend (FastAPI/Ollama RAG engine) | `backend/` | `8000` | Auto-launched by the website below; can also be run standalone with `uvicorn app.api:app --host 0.0.0.0 --port 8000` from inside `backend/`. |
| Website (Blazor Server, primary frontend) | `dotnet/RagSystemWeb/` | `5080` | `dotnet run` from this folder. |
| Legacy prototype UI (Streamlit, optional) | `frontend-streamlit/` | `8501` | Independent of the website; not needed to test the new frontend. |

Make sure 8000 and 5080 are free before starting.

## Prerequisites specific to the website

- **.NET 8 SDK** installed (`dotnet --version` should show `8.x`).
- Everything `backend/MIGRATION.md` already covers: a Python environment
  with `backend/requirements.txt` installed, Ollama installed with
  `qwen3.5:9b` pulled, Tesseract (+ `ind` language pack), Poppler on PATH.

## Data is NOT in the repo — this is the part that actually blocks testing

`backend/data/` (the corpus PDFs, the Chroma vector index, and the SQLite
session/chat database) is **deliberately git-ignored** — it's real internal
company documents and never goes to GitHub, even a private repo (see
`backend/MIGRATION.md` for the full reasoning). A fresh `git clone` gives
you all the code and **zero documents, zero index, zero chat history**.

To actually get a working, queryable system on a new machine, either:
- Copy an existing `backend/data/` folder wholesale from a machine that
  already has it (external drive, not git), or
- Get the PDFs into `backend/data/corpus/` some other way, then rebuild the
  index: `python -m app.sync_documents` from inside `backend/` (slow for
  the full ~1000-file corpus — see `MIGRATION.md` step 6 for timing notes).

Without this step the website will run, but the document list will be
empty and every question will come back with no answer.

## Machine-specific hardcoded path — must check before running elsewhere

`dotnet/RagSystemWeb/Services/BackendLauncher.cs` hardcodes the Python
interpreter it auto-launches the backend with:

```csharp
@"C:\Users\rifli\miniconda3\envs\rag_env\python.exe",
"python",
"py",
```

On another machine this exact path won't exist. It falls through to a
plain `python`/`py` from PATH next, which only works if that resolves to
an environment with `backend/requirements.txt` already installed (e.g. you
activated the right conda env before running `dotnet run` from the same
terminal). Otherwise, edit this list to point at the correct interpreter
for the machine you're deploying to.

## Running it

```
cd dotnet/RagSystemWeb
dotnet run
```

This single command also auto-launches the Python backend: it health-checks
`localhost:8000/health`, spawns `uvicorn app.api:app` if nothing answers,
and waits up to 120 seconds for it to come up before serving requests.
Open **http://localhost:5080** once it logs `Now listening on:`.

**First load after a cold start can take 30–60 seconds** (embedding/reranker
models loading, Ollama warming up) — this is normal, not a hang. Console
output during this window:

```
[BackendLauncher] Menyalakan backend API (bisa 30-60 detik pada startup pertama)...
[BackendLauncher] Backend is up.
```

If you instead see `Backend tidak merespons setelah 120 detik`, the backend
failed to start — check `backend/data/dotnet_api_launch.log` for the real
Python traceback (the website itself only ever shows this generic timeout,
never the underlying error).

## Known gotcha: Windows Smart App Control blocking the backend

If `backend/data/dotnet_api_launch.log` shows something like:

```
ImportError: DLL load failed while importing lib: An Application Control policy has blocked this file.
```

(seen on `pyarrow`'s and `chromadb`'s compiled extensions) — this is
**Windows Smart App Control** (an OS-level feature under *Settings → Privacy
& security → Windows Security → App & browser control*), not an antivirus
product, blocking unsigned native Python extensions. It is **not** fixable
from code or from this repo.

Microsoft made Smart App Control **one-way**: once turned off it cannot be
turned back on without reinstalling Windows, so this is a real
security-posture decision for whoever owns the machine, not something to
flip without thinking about it. If you hit this, that's the fix, and it's
a deliberate call each affected machine's owner has to make themselves.

## No authentication, LAN-only by design

There is no login or auth layer anywhere in this stack (backend or
website). It's built for trusted internal network use only — don't expose
port `5080` or `8000` to the public internet as-is.

## Quick troubleshooting checklist

| Symptom | Likely cause |
|---|---|
| Document list is empty, every answer says nothing found | `backend/data/corpus`/index was never populated on this machine (see "Data is NOT in the repo" above) |
| `Backend tidak merespons setelah 120 detik` | Check `backend/data/dotnet_api_launch.log` for the real Python error |
| `ImportError: DLL load failed ... Application Control` | Windows Smart App Control (see above) |
| Port already in use on startup | Something else is already bound to 8000 or 5080 — stop it first |
| "Chat baru" is disabled when you expect it to be clickable | By design — it's only enabled once the current chat has at least one message, to stop empty session rows from piling up |
