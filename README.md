# STK Online — RAG system for PT Pertamina Drilling Services Indonesia

Internal document Q&A system over ~1,177 real SOPs, contracts, and TKO/TKI/TKPA
procedures. One shared Python backend (FastAPI + ChromaDB + sqlite + local
Ollama — ingestion, retrieval, generation all live here) that any frontend
talks to over plain HTTP. Kept as independent top-level folders so each piece
can be developed and run on its own:

| Folder | Status | What it is |
|---|---|---|
| [`backend/`](backend/README.md) | **Working** | The entire RAG engine: FastAPI (`app/api.py`), ingestion, retrieval, generation, ChromaDB/sqlite, plus the real corpus data. Frontend-agnostic — nothing in here knows or cares which UI is calling it. |
| [`dotnet/`](dotnet/README.md) | **Complete — primary frontend** | A full Blazor Server (.NET 8) website matching the project's UI mockup: chat, session history, document search/preview with resizable panel and native PDF rendering, flagging, dark/light theme. |
| [`frontend-streamlit/`](frontend-streamlit/streamlit_app.py) | **Working — legacy/prototype** | The original UI: a thin Streamlit client that only calls the backend's HTTP API. Kept around as a fast iteration surface; the Blazor website above is the one being built out going forward. |

**There are two working frontend versions** — Streamlit (legacy, Python, fast to iterate on) and .NET/Blazor Server (current, the full-featured one matching the mockup). Both are pure HTTP clients of the same backend; neither owns it, and switching between them requires zero backend changes.

**The backend is not "Streamlit's backend"** — it's a standalone HTTP service. Each frontend is just one client of it, calling the same endpoints.

## Where to look

- **Running this:** [`DEPLOYMENT.md`](DEPLOYMENT.md) — prerequisites, the gitignored corpus/index data you need separately, a machine-specific path to check, and real gotchas hit while building this (including a Windows-specific one).
- **How the system is built, module by module, with diagrams:** [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **Evaluation results, every kind of testing in this project, ingestion stats, bugs found/fixed:** [`EVALUATION_RESULTS.md`](EVALUATION_RESULTS.md).
- **Current project state and mockup notes** (tech stack is already covered above, in `ARCHITECTURE.md`/`DEPLOYMENT.md`): `NOTES.md` — kept local only (gitignored), ask the repo owner if you need it.

## Directory structure

```
rag_system_stk/
├── README.md                 you are here
├── ARCHITECTURE.md           how the system is built, module by module + diagrams
├── EVALUATION_RESULTS.md     every kind of testing in this project + latest results
├── DEPLOYMENT.md             how to actually run this elsewhere
│
├── backend/                  the RAG engine -- frontend-agnostic, both UIs call this
│   ├── app/                  api.py, retrieval.py, generation.py, ingestion.py,
│   │                         database.py, config.py, eval/test scripts, ...
│   ├── README.md              backend-specific setup notes
│   └── MIGRATION.md           machine-migration history/checklist
│
├── dotnet/                   the primary frontend -- Blazor Server (.NET 8)
│   ├── README.md              what it is, how it talks to the backend
│   └── RagSystemWeb/          the actual website project
│       ├── Pages/             Index.razor (page layout), _Host.cshtml/_Layout.cshtml
│       ├── Shared/             Sidebar.razor, ChatColumn.razor, PreviewPanel.razor
│       ├── Services/           ApiClient.cs (backend HTTP calls), BackendLauncher.cs
│       ├── State/              ChatState.cs (chat/session logic, not visual)
│       ├── Models/              DTOs matching the backend's JSON shapes
│       └── wwwroot/             css/app.css (all styling), js/ (theme + resize)
│
└── frontend-streamlit/       the legacy/prototype frontend (Python)
    └── streamlit_app.py
```

This is only what's actually in the repo. Several folders exist locally but are gitignored (real internal document content, or regenerable build output) and won't show up after a fresh clone — see [`DEPLOYMENT.md`](DEPLOYMENT.md) for which ones you need to populate yourself, and `.gitignore` for the full list and why.

## Common commands

| Task | Command |
|---|---|
| **Run the website** (also auto-launches the backend) | `cd dotnet/RagSystemWeb` then `dotnet run` — open `http://localhost:5080` |
| **Run the backend by itself** | `cd backend` then `uvicorn app.api:app --host 0.0.0.0 --port 8000` |
| **Run the legacy Streamlit UI** | `cd frontend-streamlit` then `streamlit run streamlit_app.py` (also auto-launches the backend) |
| **Check the backend is actually up** | `curl http://localhost:8000/health` → `{"status":"ok","model":"qwen3.5:9b"}` |
| **Check any other endpoint** | `curl http://localhost:8000/documents` (list), `curl http://localhost:8000/sessions` (sessions) — see [`ARCHITECTURE.md`](ARCHITECTURE.md) §8 for the full endpoint table with file:line references |
| **Watch the backend's own status page in a browser** | `http://localhost:8000/monitor` |
| **Trigger a corpus resync** (new/changed/deleted files) | `curl -X POST http://localhost:8000/sync` |
| **Run backend unit tests** | Local-only (`backend/app/test_units.py` is gitignored test tooling, not in this repo) — ask the repo owner if you need it. |
| **Run website unit tests** | `cd dotnet/RagSystemWeb.Tests` then `dotnet test` |
| **Rebuild the index from scratch** | `cd backend` then `python -m app.sync_documents` |

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for prerequisites these commands assume (Ollama running, Python env, .NET SDK) and known gotchas.

## Why separate folders

`backend/` is the one thing every frontend depends on and none of them own — it stays put regardless of which UI is being worked on. `dotnet/` and `frontend-streamlit/` are both independent clients of it, built and run on their own. This split means either frontend keeps working undisturbed no matter what changes in the other — there is always a working system to show.
