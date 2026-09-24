"""Audits the currently-indexed corpus for a real, confirmed-live anomaly
class: a PDF file containing pages that structurally self-identify as a
DIFFERENT document than the file's own name/cover implies.

Confirmed on one real corpus file (27._TKI_Portal_STK_Online.pdf), whose
filename and cover page claim one document (C-035, "Portal STK Online")
but whose page 3 is an intact title-block table for a completely
different document (C-023, "Pelaksanaan Inspeksi Internal Tubular Goods
Menggunakan CAPCAM") -- wrong pages assembled into the PDF at the source.
This is a source-data problem, not something ingestion/retrieval code can
fix; this script only detects and reports it so a human can regenerate or
re-export the affected PDF correctly. See retrieval.find_foreign_document_
codes() for the detection logic and why it doesn't false-positive on
ordinary document-to-document citations in body text.

Read-only against Chroma; safe to run any time, including while the API
server is up (matches test_extraction.py's safety, unlike dump_chunks.py
or a real sync).

Usage:
    python -m app.check_corpus_integrity
"""
from __future__ import annotations

from app import database, retrieval


def run() -> list[dict]:
    database.init_db()
    flagged = []
    for source in database.list_document_sources():
        findings = retrieval.find_foreign_document_codes(source)
        if findings:
            flagged.append({"source": source, "findings": findings})
    return flagged


if __name__ == "__main__":
    flagged = run()
    if not flagged:
        print("No cross-document page-mixing anomalies found.")
    for f in flagged:
        print(f"\n{f['source']}")
        for finding in f["findings"]:
            print(
                f"  page {finding['page']}: self-identifies as "
                f"{finding['found_code']!r} -- not this file's own document number"
            )
    print(f"\n{len(flagged)} file(s) flagged for manual review.")
