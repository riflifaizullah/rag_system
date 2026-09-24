"""Extraction QA smoke test: runs extract_ingest_data() (PDF parsing, OCR,
rotation correction, graphical-page detection, VLM description, chunking)
against a sample of real corpus files WITHOUT touching Chroma/sqlite --
pure read of the PDFs plus the same in-memory pipeline sync_documents uses.

Safe to run any time, including while the API server is up (no shared
state touched), unlike dump_chunks.py or a real sync.

Usage:
    python -m app.test_extraction                # sample of 15 files
    python -m app.test_extraction 40              # sample of 40 files
    python -m app.test_extraction all             # every file in corpus
"""
from __future__ import annotations

import random
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from app import config, ingestion

DEFAULT_SAMPLE_SIZE = 15
REPORT_PATH = config.DATA_DIR / "extraction_test_report.md"


def _pick_sample(arg: str | None) -> list:
    all_files = sorted(config.CORPUS_DIR.glob("*.pdf"))
    if not all_files:
        return []
    if arg == "all":
        return all_files
    n = int(arg) if arg else DEFAULT_SAMPLE_SIZE
    n = min(n, len(all_files))
    random.seed(42)  # reproducible sample across runs
    return random.sample(all_files, n)


def run(sample_arg: str | None = None) -> dict:
    files = _pick_sample(sample_arg)
    print(f"Testing extraction on {len(files)} file(s) out of "
          f"{len(list(config.CORPUS_DIR.glob('*.pdf')))} in corpus.\n", flush=True)

    results = []
    failures = []

    for i, path in enumerate(files, start=1):
        t0 = time.time()
        try:
            data = ingestion.extract_ingest_data(path)
        except Exception as e:
            failures.append({"file": path.name, "error": repr(e), "traceback": traceback.format_exc()})
            print(f"[{i}/{len(files)}] CRASHED: {path.name} -- {e!r}", flush=True)
            continue
        elapsed = time.time() - t0

        all_chunks = data["all_chunks"]
        ocr_confidences = [
            c for c in data["ocr_confidence_by_page"].values() if c is not None and c >= 0
        ]

        result = {
            "file": path.name,
            "elapsed_seconds": round(elapsed, 1),
            "n_chunks": len(all_chunks),
            "n_pages_ocrd": len(ocr_confidences),
            "avg_ocr_confidence": round(sum(ocr_confidences) / len(ocr_confidences), 1) if ocr_confidences else None,
            "min_ocr_confidence": round(min(ocr_confidences), 1) if ocr_confidences else None,
            "n_headings": len(data["headings"]),
        }
        results.append(result)

        conf_note = f"avg OCR conf {result['avg_ocr_confidence']}" if result["avg_ocr_confidence"] is not None else "no OCR needed"
        print(
            f"[{i}/{len(files)}] {path.name} -- {result['n_chunks']} chunks, "
            f"{result['n_headings']} headings, {conf_note}, {elapsed:.1f}s",
            flush=True,
        )

    n_ok = len(results)
    n_failed = len(failures)
    zero_chunk_files = [r["file"] for r in results if r["n_chunks"] == 0]
    low_conf_files = [
        r["file"] for r in results
        if r["min_ocr_confidence"] is not None and r["min_ocr_confidence"] < database_low_conf_threshold()
    ]

    summary = {
        "n_tested": len(files),
        "n_succeeded": n_ok,
        "n_crashed": n_failed,
        "crashed_files": [f["file"] for f in failures],
        "zero_chunk_files": zero_chunk_files,
        "low_ocr_confidence_files": low_conf_files,
        "results": results,
        "failures": failures,
    }
    return summary


def database_low_conf_threshold() -> float:
    from app import database
    return database.LOW_OCR_CONFIDENCE_THRESHOLD


def write_report_md(summary: dict) -> Path:
    lines = [
        "# Extraction test report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Tested: {summary['n_tested']}  Succeeded: {summary['n_succeeded']}  Crashed: {summary['n_crashed']}",
        "",
    ]
    if summary["crashed_files"]:
        lines.append(f"**Crashed files:** {summary['crashed_files']}")
        lines.append("")
    if summary["zero_chunk_files"]:
        lines.append(f"**Zero-chunk files (check these -- likely broken extraction):** {summary['zero_chunk_files']}")
        lines.append("")
    if summary["low_ocr_confidence_files"]:
        lines.append(f"**Files with at least one low-confidence OCR page:** {summary['low_ocr_confidence_files']}")
        lines.append("")

    lines.append("## Per-file results")
    lines.append("")
    lines.append("| File | Chunks | Headings | Avg OCR conf | Min OCR conf | Seconds |")
    lines.append("|---|---|---|---|---|---|")
    for r in summary["results"]:
        lines.append(
            f"| {r['file']} | {r['n_chunks']} | {r['n_headings']} | "
            f"{r['avg_ocr_confidence']} | {r['min_ocr_confidence']} | {r['elapsed_seconds']} |"
        )

    if summary["failures"]:
        lines.append("")
        lines.append("## Failures")
        lines.append("")
        for f in summary["failures"]:
            lines.append(f"### {f['file']}")
            lines.append("```")
            lines.append(f["traceback"])
            lines.append("```")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    summary = run(arg)

    print("\n" + "=" * 60)
    print(f"Tested: {summary['n_tested']}  Succeeded: {summary['n_succeeded']}  Crashed: {summary['n_crashed']}")
    if summary["crashed_files"]:
        print(f"Crashed files: {summary['crashed_files']}")
    if summary["zero_chunk_files"]:
        print(f"Zero-chunk files (check these -- likely broken extraction): {summary['zero_chunk_files']}")
    if summary["low_ocr_confidence_files"]:
        print(f"Files with at least one low-confidence OCR page: {summary['low_ocr_confidence_files']}")

    path = write_report_md(summary)
    print(f"\nReport written to {path}")
