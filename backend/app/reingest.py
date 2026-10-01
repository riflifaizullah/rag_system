"""Force re-ingestion of specific files without touching the rest of an
already-ingested corpus.

Content-hash change detection only re-triggers when a SOURCE FILE changes,
not when the ingestion CODE changes -- if a quality bug is found and fixed
after 500 of 1000 real files are already ingested, simply re-running
`python -m app.sync_documents` will silently skip all 500 as "unchanged"
forever, still carrying the bug, since their file bytes never changed.
This clears just the named files' sqlite hash/heading record, Chroma
chunks, and chunk log, so the next sync run treats only them as new and
re-processes them with the current code -- everything else stays exactly
as it was, no need to redo an entire large real ingest over one fix.

For "redo literally everything" instead, see reset_index.py.

Never run this while uvicorn (the FastAPI server) is running -- same rule
as reset_index.py and a real sync, since this touches Chroma directly.

Usage:
    python -m app.reingest "some_file.pdf" "another_file.pdf"
    python -m app.reingest --pattern "A-0*"   # fnmatch glob against currently indexed filenames
"""
from __future__ import annotations

import argparse
import fnmatch
import sys

from app import database, ingestion, retrieval


def reingest(sources: list[str]) -> list[str]:
    database.init_db()
    collection = retrieval.get_collection()

    known = set(database.list_document_sources())
    missing = [s for s in sources if s not in known]
    if missing:
        print(f"Not currently indexed (skipping): {missing}")

    targets = [s for s in sources if s in known]
    for source in targets:
        ingestion.remove_file(source, collection)
        print(f"Cleared: {source}")

    retrieval.invalidate_sources_cache()
    return targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="Exact indexed filenames to force re-ingest")
    parser.add_argument("--pattern", help="fnmatch-style glob against currently indexed filenames, e.g. 'A-0*'")
    args = parser.parse_args()

    database.init_db()
    sources = list(args.files)
    if args.pattern:
        known = database.list_document_sources()
        matched = [s for s in known if fnmatch.fnmatch(s, args.pattern)]
        print(f"Pattern '{args.pattern}' matched {len(matched)} indexed file(s).")
        sources.extend(matched)

    if not sources:
        print("No files specified. Pass filenames and/or --pattern.")
        sys.exit(1)

    cleared = reingest(sources)
    print(
        f"\n{len(cleared)} file(s) cleared. Run `python -m app.sync_documents` to "
        f"re-ingest them with the current code -- everything else stays untouched."
    )
