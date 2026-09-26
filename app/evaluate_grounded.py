"""Benchmark harness: runs the grounded eval questions (data/eval_questions.json)
against the live system and scores retrieval / behavioral / content-correctness
metrics.

Checkpointed and resumable: confirmed live a 100-question run (each question
makes a real, sometimes slow Ollama call) can take hours, and a crash partway
through -- a real one happened, a ChromaDB/HNSW edge case on a small-chunk
document -- previously meant losing everything and restarting from question
1. Every question's result is written to data/eval_checkpoint.json the
moment it's computed, keyed by a stable "<category>:<index>" id, and a
re-run skips anything already in the checkpoint. Progress prints live
per-question ([N/100] ...) instead of staying silent until the very end,
which was the other real complaint -- no way to tell "is it working or
stuck" during a multi-hour run.

    python -m app.evaluate_grounded            # resumes from checkpoint if one exists
    python -m app.evaluate_grounded --fresh     # ignores/clears any checkpoint, starts clean
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from app import config, database, generation, retrieval

EVAL_PATH = config.DATA_DIR / "eval_questions.json"
REPORT_PATH = config.DATA_DIR / "eval_report.md"
CHECKPOINT_PATH = config.DATA_DIR / "eval_checkpoint.json"


def _overlap_ratio(expected: str, answer: str) -> float:
    expected_words = set(expected.lower().split())
    answer_words = set(answer.lower().split())
    if not expected_words:
        return 0.0
    return len(expected_words & answer_words) / len(expected_words)


def _load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}  # corrupt/partial checkpoint -- start clean rather than crash
    return {}


def _save_checkpoint(checkpoint: dict) -> None:
    # Write-to-temp-then-replace: an interruption mid-write leaves the OLD
    # checkpoint file intact rather than a half-written, unparseable one --
    # matters here specifically because this file gets rewritten after
    # every single question, not just once at the end.
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CHECKPOINT_PATH)


def _answer_text(result) -> str:
    return result.message if isinstance(result, generation.ClarificationNeeded) else result.text


def _compute_answerable(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)

    hits = retrieval.retrieve(q["question"], k=config.TOP_K)
    hit_sources = {h["source"] for h in hits}
    hit_pages = {(h["source"], h["page"]) for h in hits}

    answered = not generation.looks_like_refusal(answer_text)
    return {
        "exact_doc_hit": q["expected_source"] in hit_sources,
        "exact_page_hit": (q["expected_source"], q["expected_page"]) in hit_pages,
        "answered": answered,
        "content_score": _overlap_ratio(q["expected_text"] or "", answer_text),
    }


def _compute_refuse(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)
    return {"refused": generation.looks_like_refusal(answer_text)}


def _compute_enumerate(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result).lower()
    expected_items = q["expected_items"]
    found = sum(1 for item in expected_items if item.lower() in answer_text)
    completeness = found / max(1, len(expected_items))
    return {"completeness": completeness, "exact_match": found == len(expected_items)}


def run_evaluation(resume: bool = True) -> dict:
    database.init_db()
    with open(EVAL_PATH, encoding="utf-8") as f:
        questions = json.load(f)

    enumerate_qs = [q for q in questions if q.get("is_enumerate")]
    answerable = [q for q in questions if not q["should_refuse"] and not q.get("is_enumerate")]
    refuse_qs = [q for q in questions if q["should_refuse"]]
    total = len(answerable) + len(refuse_qs) + len(enumerate_qs)

    checkpoint = _load_checkpoint() if resume else {}
    if checkpoint:
        print(f"Resuming: {len(checkpoint)}/{total} question(s) already in checkpoint.", flush=True)
    done_so_far = len(checkpoint)
    t_run_start = time.time()

    def process(key: str, category: str, q: dict, compute_fn) -> dict:
        nonlocal done_so_far
        if key in checkpoint:
            return checkpoint[key]["data"]
        t0 = time.time()
        try:
            data = compute_fn(q)
        except Exception as exc:
            # A single bad question must not lose all progress made on the
            # other 99 -- record the failure in the checkpoint (so a re-run
            # doesn't retry it forever if it's a real, reproducible bug) and
            # keep going. The final report surfaces failed_questions
            # explicitly rather than silently underscoring the totals.
            data = {"error": repr(exc)}
        elapsed = time.time() - t0
        checkpoint[key] = {"category": category, "question": q["question"], "data": data}
        _save_checkpoint(checkpoint)
        done_so_far += 1
        status = "ERROR" if "error" in data else "ok"
        print(
            f"[{done_so_far}/{total}] ({category}) {status} {elapsed:.1f}s -- {q['question'][:70]!r}",
            flush=True,
        )
        return data

    for i, q in enumerate(answerable):
        process(f"answerable:{i}", "answerable", q, _compute_answerable)
    for i, q in enumerate(refuse_qs):
        process(f"refuse:{i}", "refuse", q, _compute_refuse)
    for i, q in enumerate(enumerate_qs):
        process(f"enumerate:{i}", "enumerate", q, _compute_enumerate)

    print(f"\nAll {total} questions processed in {time.time()-t_run_start:.1f}s this run.", flush=True)

    # ---- reassemble aggregate metrics from the checkpoint ---- #
    tp = fn = tn = fp = 0
    tp_cases, fn_cases, tn_cases, fp_cases, failed_cases = [], [], [], [], []
    exact_doc_hits = exact_page_hits = 0
    content_scores = []
    refuse_correct = 0
    enumerate_completeness_scores = []
    enumerate_exact_matches = 0

    for i in range(len(answerable)):
        entry = checkpoint[f"answerable:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        if data["exact_doc_hit"]:
            exact_doc_hits += 1
        if data["exact_page_hit"]:
            exact_page_hits += 1
        if data["answered"]:
            tp += 1
            tp_cases.append(entry["question"])
        else:
            fn += 1
            fn_cases.append(entry["question"])
        content_scores.append(data["content_score"])

    for i in range(len(refuse_qs)):
        entry = checkpoint[f"refuse:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        if data["refused"]:
            refuse_correct += 1
            tn += 1
            tn_cases.append(entry["question"])
        else:
            fp += 1
            fp_cases.append(entry["question"])

    for i in range(len(enumerate_qs)):
        entry = checkpoint[f"enumerate:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        enumerate_completeness_scores.append(data["completeness"])
        if data["exact_match"]:
            enumerate_exact_matches += 1

    n_answerable = max(len(answerable), 1)
    n_refuse = max(len(refuse_qs), 1)
    n_enumerate = max(len(enumerate_qs), 1)

    report = {
        "behavioral_accuracy_answerable": (tp + fn and tp / (tp + fn)) or 0.0,
        "behavioral_accuracy_should_refuse": refuse_correct / n_refuse,
        "retrieval_exact_document_hit": exact_doc_hits / n_answerable,
        "retrieval_exact_page_hit": exact_page_hits / n_answerable,
        "content_correctness_avg_overlap": sum(content_scores) / len(content_scores) if content_scores else 0.0,
        "enumerate_completeness_avg": (
            sum(enumerate_completeness_scores) / len(enumerate_completeness_scores)
            if enumerate_completeness_scores else 0.0
        ),
        "enumerate_exact_match_rate": enumerate_exact_matches / n_enumerate,
        "n_answerable": len(answerable),
        "n_should_refuse": len(refuse_qs),
        "n_enumerate": len(enumerate_qs),
        "n_failed": len(failed_cases),
        "failed_cases": failed_cases,
    }

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) else 0.0

    report["confusion_matrix"] = {
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "accuracy": round(accuracy, 3),
        "TP_cases": tp_cases, "FP_cases": fp_cases, "TN_cases": tn_cases, "FN_cases": fn_cases,
    }
    return report


def print_confusion_matrix(cm: dict) -> None:
    print("\nConfusion matrix (positive = system answered rather than refused):")
    print(f"                 Actual: answerable   Actual: should refuse")
    print(f"  Predicted answer      TP={cm['TP']:<10}      FP={cm['FP']:<10}")
    print(f"  Predicted refuse      FN={cm['FN']:<10}      TN={cm['TN']:<10}")
    print(f"\nPrecision={cm['precision']}  Recall={cm['recall']}  F1={cm['f1']}  Accuracy={cm['accuracy']}")
    if cm["FP_cases"]:
        print(f"\nFP (answered when it should have refused -- check for hallucination):")
        for q in cm["FP_cases"]:
            print(f"  - {q}")
    if cm["FN_cases"]:
        print(f"\nFN (refused/missed when the corpus actually has the answer -- check retrieval):")
        for q in cm["FN_cases"]:
            print(f"  - {q}")


def write_report_md(report: dict, cm: dict) -> Path:
    """Human-readable version of the same report/confusion matrix printed to
    the console -- written to a file so results survive after the terminal
    closes, same idea as chunk logs and session logs elsewhere in this app."""
    lines = [
        f"# RAG evaluation report",
        f"",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"",
        f"## Confusion matrix (positive = system answered rather than refused)",
        f"",
        f"|               | Actual: answerable | Actual: should refuse |",
        f"|---------------|---------------------|------------------------|",
        f"| Predicted answer | TP = {cm['TP']} | FP = {cm['FP']} |",
        f"| Predicted refuse | FN = {cm['FN']} | TN = {cm['TN']} |",
        f"",
        f"**Precision:** {cm['precision']}  **Recall:** {cm['recall']}  "
        f"**F1:** {cm['f1']}  **Accuracy:** {cm['accuracy']}",
        f"",
    ]
    if report.get("failed_cases"):
        lines.append("### Failed questions (error during evaluation, excluded from metrics)")
        lines.append("")
        for q, err in report["failed_cases"]:
            lines.append(f"- {q}  \n  `{err}`")
        lines.append("")
    if cm["FP_cases"]:
        lines.append("### FP -- answered when it should have refused (check for hallucination)")
        lines.append("")
        lines += [f"- {q}" for q in cm["FP_cases"]]
        lines.append("")
    if cm["FN_cases"]:
        lines.append("### FN -- refused/missed when the corpus actually has the answer (check retrieval)")
        lines.append("")
        lines += [f"- {q}" for q in cm["FN_cases"]]
        lines.append("")
    if cm["TP_cases"]:
        lines.append("### TP -- correctly answered")
        lines.append("")
        lines += [f"- {q}" for q in cm["TP_cases"]]
        lines.append("")
    if cm["TN_cases"]:
        lines.append("### TN -- correctly refused")
        lines.append("")
        lines += [f"- {q}" for q in cm["TN_cases"]]
        lines.append("")

    lines.append("## Other metrics")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report, indent=2, ensure_ascii=False))
    lines.append("```")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH


if __name__ == "__main__":
    fresh = "--fresh" in sys.argv
    if fresh and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("--fresh: cleared existing checkpoint.", flush=True)

    report = run_evaluation(resume=not fresh)
    cm = report.pop("confusion_matrix")
    print(json.dumps(report, indent=2))
    print_confusion_matrix(cm)
    path = write_report_md(report, cm)
    print(f"\nReport written to {path}")
    # Checkpoint is left in place on purpose after a successful run -- it's
    # small, harmless, and means re-running (e.g. after a question-set
    # tweak that only adds a few new questions) doesn't redo everything.
    # --fresh clears it explicitly when a truly clean run is wanted.
