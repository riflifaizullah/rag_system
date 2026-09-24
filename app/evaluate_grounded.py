"""Benchmark harness: runs the grounded eval questions (data/eval_questions.json)
against the live system and scores retrieval / behavioral / content-correctness
metrics. Scaled-down for this laptop's small test corpus (Section 10) --
not the 600-file benchmark from Section 9, which is reference-only here.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app import config, database, generation, retrieval

EVAL_PATH = config.DATA_DIR / "eval_questions.json"
REPORT_PATH = config.DATA_DIR / "eval_report.md"


def _overlap_ratio(expected: str, answer: str) -> float:
    expected_words = set(expected.lower().split())
    answer_words = set(answer.lower().split())
    if not expected_words:
        return 0.0
    return len(expected_words & answer_words) / len(expected_words)


def run_evaluation() -> dict:
    database.init_db()
    with open(EVAL_PATH, encoding="utf-8") as f:
        questions = json.load(f)

    enumerate_qs = [q for q in questions if q.get("is_enumerate")]
    answerable = [q for q in questions if not q["should_refuse"] and not q.get("is_enumerate")]
    refuse_qs = [q for q in questions if q["should_refuse"]]

    # Confusion matrix framing: "positive" = the system provided an answer
    # rather than refusing. Ground truth positive = the corpus actually has
    # the answer (an `answerable` question); ground truth negative = it
    # doesn't (a `should_refuse` question). This is a BEHAVIORAL matrix --
    # did the system correctly decide whether to answer at all -- kept
    # separate from content_correctness_avg_overlap below, which is a
    # continuous "was the substance of the answer right" score, not a
    # binary classification.
    tp = fn = tn = fp = 0
    tp_cases, fn_cases, tn_cases, fp_cases = [], [], [], []

    behavioral_correct = 0
    exact_doc_hits = 0
    exact_page_hits = 0
    content_scores = []

    for q in answerable:
        result = generation.generate_answer(q["question"])
        answer_text = result.message if isinstance(result, generation.ClarificationNeeded) else result.text

        hits = retrieval.retrieve(q["question"], k=config.TOP_K)
        hit_sources = {h["source"] for h in hits}
        hit_pages = {(h["source"], h["page"]) for h in hits}

        if q["expected_source"] in hit_sources:
            exact_doc_hits += 1
        if (q["expected_source"], q["expected_page"]) in hit_pages:
            exact_page_hits += 1

        answered = not generation.looks_like_refusal(answer_text)
        if answered:
            behavioral_correct += 1
            tp += 1
            tp_cases.append(q["question"])
        else:
            fn += 1
            fn_cases.append(q["question"])

        content_scores.append(_overlap_ratio(q["expected_text"] or "", answer_text))

    refuse_correct = 0
    for q in refuse_qs:
        result = generation.generate_answer(q["question"])
        answer_text = result.message if isinstance(result, generation.ClarificationNeeded) else result.text
        refused = generation.looks_like_refusal(answer_text)
        if refused:
            refuse_correct += 1
            tn += 1
            tn_cases.append(q["question"])
        else:
            fp += 1
            fp_cases.append(q["question"])

    # Enumerate questions: ground truth is the real heading/document count
    # from the actual generated structure (data/eval_questions.json's
    # expected_items), not the LLM's own count. This is what would have
    # caught the "list all Pasal" bug automatically -- a completeness
    # regression here means the answer is missing real items, not that the
    # LLM disagrees with itself.
    enumerate_completeness_scores = []
    enumerate_exact_matches = 0
    for q in enumerate_qs:
        result = generation.generate_answer(q["question"])
        answer_text = result.message if isinstance(result, generation.ClarificationNeeded) else result.text
        answer_lower = answer_text.lower()
        expected_items = q["expected_items"]
        found = sum(1 for item in expected_items if item.lower() in answer_lower)
        completeness = found / max(1, len(expected_items))
        enumerate_completeness_scores.append(completeness)
        if found == len(expected_items):
            enumerate_exact_matches += 1

    n_answerable = max(len(answerable), 1)
    n_refuse = max(len(refuse_qs), 1)
    n_enumerate = max(len(enumerate_qs), 1)

    report = {
        "behavioral_accuracy_answerable": behavioral_correct / n_answerable,
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
    report = run_evaluation()
    cm = report.pop("confusion_matrix")
    print(json.dumps(report, indent=2))
    print_confusion_matrix(cm)
    path = write_report_md(report, cm)
    print(f"\nReport written to {path}")
