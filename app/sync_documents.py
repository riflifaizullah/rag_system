"""Core document reconciliation (new/updated/unchanged/deleted via content
hashing). Shared by the scheduled job, the /sync endpoint, the UI button,
and direct manual runs (`python -m app.sync_documents`) -- one
implementation, four trigger paths."""
from __future__ import annotations

import json
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app import config, database, ingestion, retrieval

_scheduler: BackgroundScheduler | None = None


def sync_once(corpus_dir: Path = config.CORPUS_DIR) -> dict:
    collection = retrieval.get_collection()
    embedder = retrieval.get_embedder()

    disk_files = {p.name: p for p in corpus_dir.glob("*.pdf")}
    known_sources = set(database.list_document_sources())

    new_files, updated_files, unchanged_files, deleted_files = [], [], [], []

    for name, path in disk_files.items():
        existing_hash = database.get_document_hash(name)
        current_hash = ingestion.content_hash(path)
        if existing_hash is None:
            ingestion.ingest_file(path, collection, embedder)
            new_files.append(name)
        elif existing_hash != current_hash:
            ingestion.ingest_file(path, collection, embedder)
            updated_files.append(name)
        else:
            unchanged_files.append(name)

    for source in known_sources - set(disk_files.keys()):
        ingestion.remove_file(source, collection)
        deleted_files.append(source)

    retrieval.invalidate_sources_cache()

    return {
        "new": new_files,
        "updated": updated_files,
        "unchanged": unchanged_files,
        "deleted": deleted_files,
    }


def _scheduled_sync_once() -> None:
    # APScheduler's BackgroundScheduler runs jobs on its own dedicated
    # thread, separate from both FastAPI's request threadpool and
    # retrieval.py's single Chroma-IO thread -- funnel through the same
    # one Chroma thread everything else uses, or a periodic scheduled sync
    # would hit the exact same cross-thread ChromaDB bug the /ask and
    # /sync endpoints were fixed for.
    retrieval.run_on_chroma_thread(sync_once)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _scheduled_sync_once, "interval", minutes=config.SYNC_INTERVAL_MINUTES, id="doc_sync"
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


if __name__ == "__main__":
    database.init_db()
    print(json.dumps(sync_once(), indent=2, ensure_ascii=False))
