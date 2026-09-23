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

from app import config as app_config

API_BASE = "http://localhost:8000"
_API_LOCK_PATH = app_config.DATA_DIR / ".api_starting.lock"
_API_LOG_PATH = app_config.DATA_DIR / "api_server.log"

st.set_page_config(page_title="STK Online", page_icon="📄", layout="wide")

# Color is owned entirely by .streamlit/config.toml (base="dark" theme) now --
# this block only adds spacing/borders that Streamlit's theme doesn't cover,
# and every color used here is one of the theme's own colors (or a shade
# derived from secondaryBackgroundColor) so cards read as part of the same
# app instead of a light patch bolted onto a dark page.
st.markdown(
    """
    <style>
    .doc-row {
        padding: 10px 12px;
        margin-bottom: 10px;
        border-radius: 8px;
        background-color: #21252C;
        border: 1px solid #2D323B;
    }
    .doc-name { font-weight: 600; font-size: 0.88rem; color: #FAFAFA; margin-bottom: 2px; }
    .doc-meta { font-size: 0.76rem; color: #9AA4B2; }
    .doc-badge {
        display: inline-block;
        font-size: 0.68rem;
        font-weight: 600;
        padding: 1px 7px;
        border-radius: 999px;
        margin-bottom: 4px;
    }
    .doc-badge-contract { background-color: #1F3A5F; color: #8AB4F8; }
    .doc-badge-sop { background-color: #163A2B; color: #7EE2A8; }
    .section-title { font-size: 0.95rem; font-weight: 700; margin: 16px 0 8px 0; color: #FAFAFA; }
    .preview-text {
        background-color: #21252C;
        border: 1px solid #2D323B;
        border-radius: 8px;
        padding: 10px 12px;
        font-size: 0.82rem;
        color: #D7DBE0;
        white-space: pre-wrap;
        max-height: 220px;
        overflow-y: auto;
    }
    .session-row {
        padding: 8px 10px;
        margin-bottom: 6px;
        border-radius: 8px;
        background-color: #21252C;
        border: 1px solid #2D323B;
        border-left: 3px solid transparent;
    }
    .session-row-active { border-left: 3px solid #58A6FF; background-color: #1B2733; }
    .session-title { font-size: 0.82rem; font-weight: 600; color: #FAFAFA; margin-bottom: 2px; }
    .session-meta { font-size: 0.72rem; color: #9AA4B2; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _clean_name(filename: str) -> str:
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    return stem.replace("_", " ")


def _fetch_documents():
    try:
        resp = requests.get(f"{API_BASE}/documents", timeout=30)
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
            if r["needs_clarification"] and r["candidate_documents"]:
                clarification = {"question": question, "candidates": r["candidate_documents"]}
                break
        sources = sorted({s for r in data["results"] for s in r.get("sources", [])})

        st.session_state.messages.append(
            {"role": "assistant", "content": answer_text, "clarification": clarification, "sources": sources}
        )
    except Exception as exc:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"Permintaan gagal: {exc}", "clarification": None, "sources": []}
        )


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
    st.markdown("### 📄 STK Online")
    st.caption("Pustaka dokumen kontrak & SOP")

    st.markdown("<div class='section-title'>💬 Sesi Chat</div>", unsafe_allow_html=True)
    if st.button("+ Chat baru", use_container_width=True, key="new_chat_btn"):
        st.session_state.session_id = _create_new_session()
        st.session_state.messages = []
        st.rerun()

    sessions = _fetch_sessions()
    with st.expander(f"Riwayat sesi ({len(sessions)})", expanded=False):
        for s in sessions:
            is_active = s["session_id"] == st.session_state.session_id
            row_class = "session-row session-row-active" if is_active else "session-row"
            marker = "🟢 " if is_active else ""
            st.markdown(
                f"""
                <div class="{row_class}">
                    <div class="session-title">{marker}{s['title']}</div>
                    <div class="session-meta">{s['created_at']} · {s['total_tokens']} token</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if not is_active:
                if st.button("Buka", key=f"open_session_{s['session_id']}", use_container_width=True):
                    _switch_session(s["session_id"])
                    st.rerun()

    st.markdown("<div class='section-title'>📚 Dokumen</div>", unsafe_allow_html=True)
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
    contracts = [d for d in docs if d["type"] == "contract"]
    sops = [d for d in docs if d["type"] == "sop"]

    def render_doc_section(title: str, badge_class: str, items: list[dict]):
        st.markdown(f"<div class='section-title'>{title} ({len(items)})</div>", unsafe_allow_html=True)
        for doc in items:
            st.markdown(
                f"""
                <div class="doc-row">
                    <span class="doc-badge {badge_class}">{doc['type'].upper()}</span>
                    <div class="doc-name">{_clean_name(doc['filename'])}</div>
                    <div class="doc-meta">{doc['page_count']} hal · {doc['size_kb']} KB</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("Pratinjau", key=f"preview_btn_{doc['filename']}", use_container_width=True):
                st.session_state.preview_doc = doc["filename"]

    render_doc_section("📑 Kontrak", "doc-badge-contract", contracts)
    render_doc_section("🦺 SOP", "doc-badge-sop", sops)

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
            sources = msg.get("sources")
            if sources:
                st.caption("📄 Sumber: " + ", ".join(sources))
            clarification = msg.get("clarification")
            is_last = idx == len(st.session_state.messages) - 1
            if clarification and is_last:
                st.caption("Pilih salah satu dokumen berikut, lalu ketik pertanyaan Anda:")
                for c_idx, candidate in enumerate(clarification["candidates"]):
                    label = candidate
                    if candidate == st.session_state.selected_document:
                        label = f"✓ {candidate}"
                    if st.button(label, key=f"cand_{idx}_{c_idx}"):
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

    question = st.chat_input("Tanyakan sesuatu tentang dokumen kontrak/SOP...")
    if question:
        with st.spinner("Memproses..."):
            submit_question(question)
        st.rerun()

with col_preview:
    st.markdown("#### Pratinjau Dokumen")
    if not st.session_state.preview_doc:
        st.info("Pilih dokumen dari daftar di sidebar untuk melihat pratinjau.")
    else:
        filename = st.session_state.preview_doc
        with st.spinner("Memuat pratinjau..."):
            preview = _fetch_preview(filename)

        if "error" in preview:
            st.error(f"Gagal memuat pratinjau: {preview['error']}")
        else:
            st.markdown(f"**{_clean_name(filename)}**")
            st.caption(f"{preview['page_count']} halaman")

            if preview.get("thumbnail_png_base64"):
                st.image(base64.b64decode(preview["thumbnail_png_base64"]), use_column_width=True)
            else:
                st.caption("Thumbnail tidak tersedia.")

            st.markdown("**Cuplikan halaman 1:**")
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
