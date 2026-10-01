"""Clears all ingested state -- Chroma chunks, sqlite document/heading
tracking, and chunk log files -- so the next `python -m app.sync_documents`
run treats the whole corpus as new. Does NOT touch chat sessions/history or
eval reports; only document-ingestion state.

Clearing sqlite's document/heading records matters as much as clearing
Chroma: sync_once() decides "new/updated/unchanged" by comparing each
file's current content hash against what's stored in sqlite. If only
Chroma were cleared, every file would still show a matching hash and get
skipped as "unchanged" even though its actual chunks are gone -- silently
leaving the whole corpus unindexed.

Never run this while uvicorn (the FastAPI server) is running -- same rule
as dump_chunks.py and a real sync, since this touches Chroma directly.

Usage:
    python -m app.reset_index
"""
from __future__ import annotations

import shutil

import chromadb

from app import config, database, retrieval


def reset() -> None:
    database.init_db()

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    try:
        client.delete_collection(name=retrieval._COLLECTION_NAME)
        print(f"Dropped Chroma collection '{retrieval._COLLECTION_NAME}'.")
    except Exception as e:
        print(f"No existing Chroma collection to drop ({e!r}).")

    sources = database.list_document_sources()
    for source in sources:
        database.delete_document(source)
    print(f"Cleared {len(sources)} document/heading record(s) from sqlite.")

    if config.CHUNK_LOGS_DIR.exists():
        n = len(list(config.CHUNK_LOGS_DIR.glob("*.md")))
        shutil.rmtree(config.CHUNK_LOGS_DIR)
        config.CHUNK_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Deleted {n} chunk log file(s).")

    retrieval.invalidate_sources_cache()
    print("Done. Next `python -m app.sync_documents` will treat the whole corpus as new.")


if __name__ == "__main__":
    reset()
