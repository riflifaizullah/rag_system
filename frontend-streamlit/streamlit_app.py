"""Streamlit frontend: chat UI + document library/preview. Calls the local
FastAPI API over HTTP (matching the client/server shape used in the PoC,
Section 5 of the rebuild spec) -- the UI never reads the corpus directory
or ChromaDB directly, everything goes through /ask, /sync, /documents."""
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
import streamlit as st

# `app/` (the shared backend engine) lives in ../backend, a sibling folder,
# not next to this file -- Streamlit only puts this script's own directory
# on sys.path, so the import below needs backend/ added explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config as app_config

API_BASE = "http://localhost:8000"
_API_LOCK_PATH = app_config.DATA_DIR / ".api_starting.lock"
_API_LOG_PATH = app_config.DATA_DIR / "api_server.log"

st.set_page_config(page_title="RAG SYSTEM", page_icon="📄", layout="wide")

# Color is owned entirely by .streamlit/config.toml (base="dark" theme) now --
# this block only adds spacing/borders that Streamlit's theme doesn't cover,
# and every color used here is one of the theme's own colors (or a shade
# derived from secondaryBackgroundColor) so cards read as part of the same
# app instead of a light patch bolted onto a dark page.
st.markdown(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@700;800&display=swap" rel="stylesheet">
    <style>
    .brand-title { font-family: 'Plus Jakarta Sans', sans-serif; }
    .section-title { font-size: 0.95rem; font-weight: 700; margin: 16px 0 8px 0; color: #F1F2F5; }
    /* Document list: real st.button widgets, restyled into compact rows.
       Scoped via :has() on the element-container wrapping the
       #doc-bubbles-anchor marker (this Streamlit version has no
       st.container(key=...) for a stable class hook, and a plain
       "~ div" sibling selector doesn't reach far enough up Streamlit's
       own nesting to find real siblings) so the session-row/"Sinkronkan"
       buttons elsewhere in the sidebar are untouched -- only buttons
       rendered after the anchor's own block are affected. The label text
       is wrapped in its own <p>, which needs the no-wrap/ellipsis rules
       directly (they don't inherit reliably from the button). */
    .element-container:has(#doc-bubbles-anchor) ~ div [data-testid="stButton"] button {
        padding: 6px 12px;
        border-radius: 8px;
        background-color: #202127;
        border: 1px solid #2A2C36;
        min-height: 0;
        text-align: left;
        justify-content: flex-start;
    }
    .element-container:has(#doc-bubbles-anchor) ~ div [data-testid="stButton"] button p {
        font-size: 0.78rem;
        font-weight: 500;
        color: #C9CCD6;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        width: 100% !important;
        max-width: 100%;
        line-height: 1.4;
        margin: 0;
        text-align: left;
    }
    .element-container:has(#doc-bubbles-anchor) ~ div [data-testid="stButton"] button:hover p {
        color: #F1F2F5;
    }
    .element-container:has(#doc-bubbles-anchor) ~ div [data-testid="stButton"] button:hover {
        border-color: #5B6EF5;
    }
    .element-container:has(#doc-bubbles-anchor) ~ div [data-testid="stButton"] { margin-bottom: 6px; }
    #doc-bubbles-anchor { display: block; width: 100%; height: 0; clear: both; }
    /* Candidate-document bar: glued to Streamlit's own fixed chat_input bar
       (~76px tall) via position:fixed, not position:sticky -- sticky only
       stays pinned while ITS OWN scrolling container is in view, so it can
       still detach and scroll away on a long conversation; fixed anchors to
       the viewport itself, same as chat_input, so the two always move
       together regardless of scroll position ("+ div": immediate sibling
       only, so this can't reach past its own container into chat_input's
       own unrelated widget). Spans the main content area (sidebar
       excluded) rather than matching the chat column exactly -- Streamlit
       gives chat_input itself special column-width handling that a plain
       div can't replicate without JS; a few pixels of overlap into the
       preview column is a cosmetic tradeoff, not a functional one. */
    .element-container:has(#candidates-anchor) + div {
        position: fixed;
        left: calc(21rem + 32px);
        right: 32px;
        /* chat_input's real rendered height runs taller than a first guess
           (76px) accounted for -- confirmed live, the bar was overlapping
           and partly covering the input box instead of sitting above it.
           118px leaves real clearance; if it still looks tight against a
           particular font size/zoom, raise this further rather than
           trying to compute chat_input's exact height in pure CSS. */
        bottom: 118px;
        z-index: 999;
        background-color: #202127;
        border: 1px solid #2A2C36;
        border-radius: 10px;
        padding: 8px 12px 4px 12px;
        margin: 0;
        box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.25);
    }
    #candidates-anchor { display: block; width: 100%; height: 0; clear: both; }
    /* The candidate filenames themselves: one horizontally scrolling row
       instead of N equal-width columns stacking full-width. Built on
       st.columns() rather than a plain st.container() -- a bare container's
       wrapping DOM depth turned out unpredictable to reach with :has() (the
       row rendered stacked vertically instead of flexed, confirmed live) --
       because st.columns() always renders as the stable, well-documented
       [data-testid="stHorizontalBlock"] / [data-testid="stColumn"] pair. */
    .element-container:has(#candidates-scroll-anchor) + div[data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap;
        overflow-x: auto;
        gap: 6px;
        padding-bottom: 6px;
    }
    .element-container:has(#candidates-scroll-anchor) + div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: unset !important;
    }
    .element-container:has(#candidates-scroll-anchor) + div[data-testid="stHorizontalBlock"] [data-testid="stButton"] button {
        white-space: nowrap;
    }
    #candidates-scroll-anchor { display: block; width: 100%; height: 0; clear: both; }
    .ai-disclaimer {
        font-size: 0.72rem;
        color: #9CA0AC;
        margin-top: 6px;
    }
    .empty-state {
        background-color: #202127;
        border: 1px solid #2A2C36;
        border-radius: 10px;
        padding: 14px 16px;
        font-size: 0.85rem;
        color: #9CA0AC;
    }
    .preview-header {
        margin-bottom: 10px;
    }
    .preview-doc-title {
        font-size: 0.95rem;
        font-weight: 700;
        color: #F1F2F5;
        line-height: 1.35;
    }
    .preview-doc-meta {
        font-size: 0.78rem;
        color: #9CA0AC;
        margin-top: 2px;
    }
    .preview-section-label {
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #9CA0AC;
        margin: 14px 0 6px 0;
    }
    .preview-text {
        background-color: #202127;
        border: 1px solid #2A2C36;
        border-radius: 8px;
        padding: 10px 12px;
        font-size: 0.82rem;
        color: #D5D8E0;
        white-space: pre-wrap;
        max-height: 220px;
        overflow-y: auto;
    }

    /* Riwayat/Dokumen segmented control -- real <input type="radio"> elements
       underneath, so :checked works directly, same trick as the mockup's
       CSS-custom-property approach but against Streamlit's actual DOM
       (BaseWeb radio: label[data-baseweb="radio"] > first div is the visual
       circle, an <input> follows, then the text). */
    .stRadio [role="radiogroup"] {
        display: flex;
        gap: 4px;
        padding: 3px;
        background: #16171C;
        border-radius: 10px;
        margin-bottom: 4px;
    }
    .stRadio [role="radiogroup"] label[data-baseweb="radio"] {
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 7px 0;
        border-radius: 8px;
        background: transparent;
        cursor: pointer;
        transition: background .15s ease;
    }
    .stRadio [role="radiogroup"] label[data-baseweb="radio"] > div:first-child {
        display: none;
    }
    .stRadio [role="radiogroup"] label[data-baseweb="radio"] p {
        font-size: 12px;
        font-weight: 700;
        color: #656B76;
        margin: 0;
    }
    .stRadio [role="radiogroup"] label[data-baseweb="radio"]:has(input:checked) {
        background: #202127;
    }
    .stRadio [role="radiogroup"] label[data-baseweb="radio"]:has(input:checked) p {
        color: #F1F2F5;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _clean_name(filename: str) -> str:
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    return stem.replace("_", " ")


def _bubble_label(filename: str, max_chars: int = 14) -> str:
    """Short label for the compact bubble button -- real filenames run 60-
    100+ characters, which would break a small pill's layout. Full name
    stays available via the button's own hover tooltip (help=)."""
    name = _clean_name(filename)
    return name if len(name) <= max_chars else name[: max_chars - 1].rstrip() + "…"


def _fetch_documents():
    try:
        resp = requests.get(f"{API_BASE}/documents", timeout=90)
        resp.raise_for_status()
        return resp.json().get("documents", [])
    except Exception:
        return []


def _fetch_preview(filename: str):
    try:
        resp = requests.get(f"{API_BASE}/documents/{filename}/preview", timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_sessions():
    try:
        resp = requests.get(f"{API_BASE}/sessions", timeout=15)
        resp.raise_for_status()
        return resp.json().get("sessions", [])
    except Exception:
        return []


def _create_new_session() -> str:
    resp = requests.post(f"{API_BASE}/sessions", timeout=15)
    resp.raise_for_status()
    return resp.json()["session_id"]


def _load_session_history(session_id: str) -> list[dict]:
    """Reconstructs the Streamlit message list from the DB-backed history --
    the same tables `create_session()`/`save_message()` always wrote to,
    just never surfaced anywhere until now. Historical messages don't carry
    clarification metadata (that's only meaningful for the just-answered
    live turn) or per-message sources (not stored in the messages table,
    only in answer_log), so both are left empty on reload."""
    try:
        resp = requests.get(f"{API_BASE}/sessions/{session_id}/history", timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("messages", [])
    except Exception:
        return []
    return [
        {"role": r["role"], "content": r["content"], "clarification": None, "sources": []}
        for r in rows
    ]


def _switch_session(session_id: str):
    st.session_state.session_id = session_id
    st.session_state.messages = _load_session_history(session_id)


def submit_question(question: str):
    st.session_state.messages.append({"role": "user", "content": question, "clarification": None})
    selected = st.session_state.get("selected_document")
    sent_question = f"{question} untuk dokumen {selected}" if selected else question
    try:
        resp = requests.post(
            f"{API_BASE}/ask",
            json={"question": sent_question, "session_id": st.session_state.session_id},
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()

        answer_text = "\n\n".join(r["answer"] for r in data["results"])
        clarification = None
        for r in data["results"]:
            # Fires for both the hard-block case (needs_clarification, no
            # answer given) and the soft case (a real answer plus other
            # documents that also looked relevant) -- same candidate list
            # shape either way, so the UI renders it identically.
            if r["candidate_documents"]:
                clarification = {"question": question, "candidates": r["candidate_documents"]}
                break
        sources = sorted({s for r in data["results"] for s in r.get("sources", [])})
        # Only a single-sub-question turn maps to exactly one answer_log row --
        # a multi-question or multi-document-combined turn has several (or
        # none), and there is no sound way to flag "this part of the combined
        # answer" from one button, so the flag control only ever appears for
        # the unambiguous single-log case.
        log_ids = [r["log_id"] for r in data["results"] if r.get("log_id") is not None]

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer_text,
                "clarification": clarification,
                "sources": sources,
                "log_ids": log_ids,
            }
        )
        st.session_state.hide_candidates = False
    except Exception as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Permintaan gagal: {exc}", "clarification": None, "sources": []}
        )


_FLAG_LABELS = {
    "hallucinated_fact": "Informasi tidak akurat / mengada-ada",
    "wrongly_refused": "Salah menolak menjawab",
    "bad_retrieval": "Sumber yang diambil salah/tidak relevan",
    "incomplete_or_unclear": "Jawaban tidak lengkap / tidak jelas",
    "other": "Lainnya",
}


def _flag_answer(log_id: int, category: str, corrected_answer: str) -> bool:
    try:
        resp = requests.post(
            f"{API_BASE}/flag/{log_id}",
            params={"category": category, "corrected_answer": corrected_answer or None},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def render_flag_control(log_id: int) -> None:
    """Backend for this (database.flag_answer / POST /flag/{log_id}) already
    existed and worked -- this was the missing piece: nothing in the UI ever
    called it, so a human reviewer had no way to actually flag a bad answer.
    Already-flagged rows just show a small confirmation instead of the form
    again (tracked client-side in session_state; the row itself doesn't need
    re-reading from the server for this)."""
    flagged_ids = st.session_state.setdefault("flagged_log_ids", set())
    if log_id in flagged_ids:
        st.caption("🚩 Ditandai -- terima kasih atas masukannya.")
        return

    with st.popover("🚩 Tandai jawaban ini"):
        category = st.selectbox(
            "Apa yang salah dengan jawaban ini?",
            options=list(_FLAG_LABELS.keys()),
            format_func=lambda c: _FLAG_LABELS[c],
            key=f"flagcat_{log_id}",
        )
        corrected = st.text_area(
            "Jawaban yang seharusnya (opsional)", key=f"flagcorrect_{log_id}", height=80
        )
        if st.button("Kirim", key=f"flagsubmit_{log_id}", use_container_width=True):
            if _flag_answer(log_id, category, corrected):
                flagged_ids.add(log_id)
                st.rerun()
            else:
                st.error("Gagal mengirim tanda. Coba lagi.")


def _api_is_up() -> bool:
    try:
        return requests.get(f"{API_BASE}/health", timeout=3).ok
    except Exception:
        return False


def _spawn_api_server() -> None:
    env = os.environ.copy()
    env.setdefault("TESSDATA_PREFIX", str(Path(os.environ.get("LOCALAPPDATA", "")) / "tessdata"))
    log_file = open(_API_LOG_PATH, "a", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api:app", "--host", app_config.API_HOST, "--port", str(app_config.API_PORT)],
        cwd=str(app_config.BASE_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _ensure_api_running() -> None:
    """Streamlit is a thin HTTP client for the FastAPI backend and never
    touches ChromaDB/SQLite itself -- but the two have always had to be
    launched as separate manual steps, which is easy to forget and then
    surfaces as a raw ConnectionError deep in a callback instead of a clear
    message. Auto-launch the backend here if it isn't already up, guarded
    by a lock file so multiple Streamlit reruns/browser tabs don't race to
    spawn duplicate servers. Running the backend on its own with plain
    uvicorn (as documented in the README) still works exactly as before --
    this only adds a fallback, it doesn't replace that path."""
    if _api_is_up():
        return

    lock_fresh = _API_LOCK_PATH.exists() and (time.time() - _API_LOCK_PATH.stat().st_mtime) < 130
    if not lock_fresh:
        _API_LOCK_PATH.write_text(str(time.time()))
        _spawn_api_server()

    with st.spinner("Menyalakan backend API (bisa 30-60 detik pada startup pertama)..."):
        deadline = time.time() + 120
        while time.time() < deadline:
            if _api_is_up():
                try:
                    _API_LOCK_PATH.unlink()
                except Exception:
                    pass
                return
            time.sleep(2)

    st.error(
        "Backend API tidak merespons setelah 120 detik. Cek log di "
        f"`{_API_LOG_PATH}`, atau jalankan manual: "
        "`.venv\\Scripts\\uvicorn.exe app.api:app --host 0.0.0.0 --port 8000`"
    )
    st.stop()


_ensure_api_running()

if "session_id" not in st.session_state:
    # a real session from the first load, not None-until-first-question --
    # so the sidebar's session list and "current session" indicator are
    # meaningful even before the user has typed anything.
    st.session_state.session_id = _create_new_session()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "preview_doc" not in st.session_state:
    st.session_state.preview_doc = None
if "documents_cache" not in st.session_state:
    st.session_state.documents_cache = _fetch_documents()
if "selected_document" not in st.session_state:
    # A document picked from a candidate list scopes every subsequent typed
    # question until cleared -- selecting and asking are two separate
    # actions now, not one click that blindly resubmits the same original
    # question (the old behavior couldn't ask a *different* follow-up about
    # the file you just picked without retyping everything).
    st.session_state.selected_document = None

with st.sidebar:
    st.markdown("<h3 class='brand-title'>📄 RAG SYSTEM</h3>", unsafe_allow_html=True)
    st.caption("PDSI · Pustaka Dokumen")

    if st.button("+ Chat baru", use_container_width=True, key="new_chat_btn"):
        st.session_state.session_id = _create_new_session()
        st.session_state.messages = []
        st.rerun()

    sessions = _fetch_sessions()  # fetched unconditionally -- also used below for the active-session caption

    sidebar_tab = st.radio(
        "Bagian sidebar",
        options=["Riwayat", "Dokumen"],
        index=1,
        horizontal=True,
        key="sidebar_tab",
        label_visibility="collapsed",
    )

    if sidebar_tab == "Riwayat":
        st.markdown(f"<div class='section-title'>💬 Riwayat Sesi ({len(sessions)})</div>", unsafe_allow_html=True)
        for s in sessions:
            is_active = s["session_id"] == st.session_state.session_id
            label = f"{s['title']} · {s['total_tokens']} token"
            # The row itself is the open control -- no separate "Buka"
            # button. Active session uses Streamlit's own primary-color
            # button styling to stand out, instead of a hand-rolled dot/
            # highlight (native platform feature over custom CSS), and is
            # disabled since it's already open.
            if st.button(
                label,
                key=f"open_session_{s['session_id']}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
                disabled=is_active,
                help=s["created_at"],
            ):
                _switch_session(s["session_id"])
                st.rerun()

    else:  # "Dokumen"
        if st.button("🔄 Sinkronkan dokumen", use_container_width=True):
            with st.spinner("Menyinkronkan..."):
                try:
                    resp = requests.post(f"{API_BASE}/sync", timeout=300)
                    resp.raise_for_status()
                    result = resp.json()
                    n_new = len(result.get("new", []))
                    n_updated = len(result.get("updated", []))
                    n_deleted = len(result.get("deleted", []))
                    st.success(f"{n_new} baru, {n_updated} diperbarui, {n_deleted} dihapus")
                    st.session_state.documents_cache = _fetch_documents()
                except Exception as exc:
                    st.error(f"Sinkronisasi gagal: {exc}")

        docs = st.session_state.documents_cache
        st.markdown(f"<div class='section-title'>{len(docs)} dokumen</div>", unsafe_allow_html=True)

        # Plain vertical list, one filename per row -- no grid, no type labels.
        # With ~1000+ real documents this scrolls, but stays simple to scan.
        st.markdown('<div id="doc-bubbles-anchor"></div>', unsafe_allow_html=True)
        for doc in docs:
            full_name = _clean_name(doc["filename"])
            if st.button(
                _bubble_label(doc["filename"], max_chars=32),
                key=f"doc_bubble_{doc['filename']}",
                help=f"{full_name} · {doc['page_count']} hal · {doc['size_kb']} KB",
                use_container_width=True,
            ):
                st.session_state.preview_doc = doc["filename"]

col_chat, col_preview = st.columns([2, 1])

with col_chat:
    st.markdown("#### Tanya Jawab Dokumen")
    _current = next(
        (s for s in sessions if s["session_id"] == st.session_state.session_id), None
    )
    if _current:
        st.caption(f"Sesi aktif: {_current['title']} · {_current['total_tokens']} token terpakai")
    else:
        st.caption("Sesi baru · 0 token terpakai")

    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                st.markdown(
                    "<div class='ai-disclaimer'>⚠️ AI dapat membuat kesalahan. Mohon periksa kembali "
                    "dokumen sumber sebelum digunakan.</div>",
                    unsafe_allow_html=True,
                )
            sources = msg.get("sources")
            if sources:
                st.caption("📄 Sumber: " + ", ".join(sources))
            log_ids = msg.get("log_ids") or []
            if msg["role"] == "assistant" and len(log_ids) == 1:
                render_flag_control(log_ids[0])

    # Candidate-document buttons (from either a hard clarification block or
    # a soft "here's an answer, but other documents also matched" note) are
    # rendered once, pinned just above the chat input via the sticky-bar CSS
    # below -- not inline in the message loop -- so they stay visible
    # regardless of scroll position, like a persistent follow-up bar. Only
    # the latest turn's candidates are ever shown, and the user can dismiss
    # them with the X without losing the ability to just type a document name.
    if "hide_candidates" not in st.session_state:
        st.session_state.hide_candidates = False
    last_msg = st.session_state.messages[-1] if st.session_state.messages else None
    last_clarification = (last_msg or {}).get("clarification") if last_msg and last_msg["role"] == "assistant" else None
    if last_clarification and not st.session_state.hide_candidates:
        st.markdown('<div id="candidates-anchor"></div>', unsafe_allow_html=True)
        with st.container():
            cap_col, close_col = st.columns([8, 1])
            with cap_col:
                st.caption("Dokumen relevan lainnya -- pilih salah satu untuk fokus, atau abaikan:")
            with close_col:
                if st.button("✕", key="dismiss_candidates"):
                    st.session_state.hide_candidates = True
                    st.rerun()
            # A row of up to 9 real filenames doesn't fit as equal-width
            # columns without squeezing every label unreadably thin -- built
            # on st.columns() specifically (not a plain st.container(), which
            # turned out too fragile to target with :has() CSS reliably)
            # because st.columns() always renders as the well-known, stable
            # [data-testid="stHorizontalBlock"]/[data-testid="stColumn"] pair,
            # which the CSS below turns into a nowrap, horizontally
            # scrolling row instead of an equal-width split.
            st.markdown('<div id="candidates-scroll-anchor"></div>', unsafe_allow_html=True)
            btn_cols = st.columns(len(last_clarification["candidates"]))
            for c_idx, candidate in enumerate(last_clarification["candidates"]):
                label = f"✓ {_bubble_label(candidate, max_chars=28)}" if candidate == st.session_state.selected_document else _bubble_label(candidate, max_chars=28)
                with btn_cols[c_idx]:
                    if st.button(label, key=f"cand_sticky_{candidate}", help=_clean_name(candidate)):
                        st.session_state.selected_document = candidate
                        st.rerun()

    if st.session_state.selected_document:
        chip_col, clear_col = st.columns([5, 1])
        with chip_col:
            st.caption(f"📄 Bertanya tentang: {st.session_state.selected_document}")
        with clear_col:
            if st.button("✕", key="clear_selected_doc"):
                st.session_state.selected_document = None
                st.rerun()

    if "is_answering" not in st.session_state:
        st.session_state.is_answering = False

    # Slow answers (full-document-fallback path, 20-40+ seconds) left a real
    # window for a user to submit a second question before the first
    # finished -- confirmed live: two submissions 43s apart, both actually
    # answered correctly and saved to sqlite, but the live view never showed
    # either (a Streamlit script-run overlap issue, not a backend failure --
    # see NOTES.md for the full diagnosis). Disabling input while a request
    # is in flight removes the overlapping-submission trigger for that bug.
    question = st.chat_input(
        "Tanyakan sesuatu tentang dokumen kontrak/SOP...",
        disabled=st.session_state.is_answering,
    )
    if question:
        st.session_state.is_answering = True
        try:
            with st.spinner("Memproses..."):
                submit_question(question)
        finally:
            st.session_state.is_answering = False
        st.rerun()

with col_preview:
    title_col, close_col = st.columns([5, 1])
    with title_col:
        st.markdown("#### Pratinjau Dokumen")

    if not st.session_state.preview_doc:
        st.markdown(
            "<div class='empty-state'>Pilih dokumen dari daftar di sidebar untuk melihat pratinjau.</div>",
            unsafe_allow_html=True,
        )
    else:
        with close_col:
            if st.button("✕", key="close_preview", help="Tutup pratinjau", use_container_width=True):
                st.session_state.preview_doc = None
                st.rerun()

        filename = st.session_state.preview_doc
        with st.spinner("Memuat pratinjau..."):
            preview = _fetch_preview(filename)

        if "error" in preview:
            st.error(f"Gagal memuat pratinjau: {preview['error']}")
        else:
            st.markdown(
                f"""<div class='preview-header'>
                    <div class='preview-doc-title'>{_clean_name(filename)}</div>
                    <div class='preview-doc-meta'>{preview['page_count']} halaman</div>
                </div>""",
                unsafe_allow_html=True,
            )

            if preview.get("thumbnail_png_base64"):
                st.image(base64.b64decode(preview["thumbnail_png_base64"]), use_column_width=True)
            else:
                st.caption("Thumbnail tidak tersedia.")

            st.markdown("<div class='preview-section-label'>Cuplikan halaman 1</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='preview-text'>{preview.get('text_snippet', '')}</div>", unsafe_allow_html=True)

            try:
                dl_resp = requests.get(f"{API_BASE}/documents/{filename}/download", timeout=30)
                dl_resp.raise_for_status()
                st.download_button(
                    "⬇️ Unduh PDF",
                    data=dl_resp.content,
                    file_name=filename,
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception as exc:
                st.caption(f"Unduhan tidak tersedia: {exc}")
