"""Standalone read-only debug tool: regenerates data/chunk_logs/<file>.md for
one document or the whole corpus straight from Chroma's current state.

Chunk logs are normally written automatically every time a document is
(re-)ingested (see ingestion.write_chunk_log_md, called from
write_ingest_data) -- this script exists for backfilling logs for documents
that were already indexed before that existed, or for regenerating one
without re-running OCR.

Never run this while uvicorn (the FastAPI server) is running -- both this
script and the server touch ChromaDB directly, and concurrent access
corrupted the on-disk HNSW index once already (see retrieval.py's
_chroma_thread comment). Stop the server first, run this, then restart it.

Usage:
    python -m app.dump_chunks                 # every document currently indexed
    python -m app.dump_chunks some_file.pdf    # just that one document
"""
from __future__ import annotations

import sys
from pathlib import Path

from app import database, ingestion, retrieval


def dump_source(source: str) -> Path:
    collection = retrieval.get_collection()
    fetched = collection.get(where={"source": source}, include=["documents", "metadatas"])
    docs = fetched.get("documents", [])
    metas = fetched.get("metadatas", [])
    records = sorted(
        (
            {
                "page": meta.get("page"),
                "chunk_index": meta.get("chunk_index"),
                "ocr_confidence": meta.get("ocr_confidence"),
                "text": text,
            }
            for meta, text in zip(metas, docs)
        ),
        key=lambda r: (r["page"], r["chunk_index"]),
    )
    return ingestion.write_chunk_log_md(source, records)


def dump_all() -> list[Path]:
    return [dump_source(s) for s in database.list_document_sources()]


if __name__ == "__main__":
    written = [dump_source(sys.argv[1])] if len(sys.argv) > 1 else dump_all()

    for p in written:
        print(f"Wrote {p}")
    print(f"\n{len(written)} document(s) written to data/chunk_logs/")
