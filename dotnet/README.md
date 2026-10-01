# STK Online — Blazor Server website

The current primary frontend — a complete Blazor Server (.NET 8) website in
[`RagSystemWeb/`](RagSystemWeb/), built to match the project's UI mockup and
covering the full feature set: chat with the real backend, session history,
document search/preview (with a resizable panel and native in-browser PDF
rendering), ambiguous-question candidate resolution, answer flagging, and a
dark/light theme toggle.

See [`../DEPLOYMENT.md`](../DEPLOYMENT.md) for how to actually run this —
prerequisites, the gitignored corpus/index data you'll need separately, a
machine-specific hardcoded path to check, and a real Windows gotcha
(Smart App Control) that can block the backend from starting.

## How it talks to the backend

Pure HTTP client of the same FastAPI contract documented in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) §7 — nothing in `backend/` was
written with this frontend in mind, and nothing here touches ChromaDB/sqlite
directly. `Services/ApiClient.cs` is the only place that calls the backend.

One small backend change *was* needed and made: `document_download()` in
`backend/app/api.py` sets `Content-Disposition: inline` instead of the
Starlette default `attachment`, so this frontend's `<iframe>` preview panel
renders PDFs natively instead of triggering a download prompt.

## Auto-launching the backend

`Services/BackendLauncher.cs` health-checks `http://localhost:8000/health`
on startup and spawns `uvicorn app.api:app` itself if nothing answers (an
atomic lock file prevents two instances racing to spawn it twice). This is
why `dotnet run` alone is enough to get a working system — see
`../DEPLOYMENT.md` for the one machine-specific path inside this file you
may need to edit.

## Relationship to `frontend-streamlit/`

Both are independent HTTP clients of the exact same backend contract —
see [`../README.md`](../README.md) for current status of each. Streamlit is
kept as the original prototyping surface; this is the one built out to the
full mockup and intended as the primary frontend going forward.
