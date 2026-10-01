"""Benchmark harness: runs the grounded eval questions (data/eval_questions.json)
against the live system and scores retrieval / behavioral / content-correctness
metrics across all five categories:
  1. answerable (exact heading queries)
  2. procedural (instructional/step queries without quotes)
  3. cross_document (multi-document discovery queries)
  4. should_refuse (off-topic + non-existent identifiers)
  5. enumerate (deterministic category listings)

Checkpointed and resumable: writes each result to data/eval_checkpoint.json
immediately, so interruptions or re-runs resume seamlessly.

Usage:
    python -m app.evaluate_grounded                  # resumes from checkpoint
    python -m app.evaluate_grounded --fresh          # clears checkpoint, starts clean
    python -m app.evaluate_grounded --tag catchup    # uses eval_questions_catchup.json,
                                                      # checkpointed/reported separately
                                                      # (eval_checkpoint_catchup.json,
                                                      # eval_report_catchup.md) -- never
                                                      # touches the default run's files
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
            return {}
    return {}


def _save_checkpoint(checkpoint: dict) -> None:
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CHECKPOINT_PATH)


def _answer_text(result) -> str:
    return result.message if isinstance(result, generation.ClarificationNeeded) else result.text


def _really_answered(result, answer_text: str) -> bool:
    """True only if the system gave a real answer -- neither a hard refusal
    NOR a clarification request. Root cause this fixes (found while
    designing a bare-identifier-ambiguity eval category): looks_like_refusal()
    only matches refusal PHRASING ('tidak ditemukan', etc), so a
    ClarificationNeeded result ("Mohon sebutkan dokumen yang dimaksud")
    never matched it and was silently counted as "answered" everywhere this
    was inlined as `not looks_like_refusal(...)` -- wrongly scoring a
    correct clarification as a hit on Answerable/Procedural questions, and
    as a hallucinated FP on should-refuse questions that should have
    triggered clarification instead of a flat refusal."""
    if isinstance(result, generation.ClarificationNeeded):
        return False
    return not generation.looks_like_refusal(answer_text)


def _compute_answerable(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)

    hits = retrieval.retrieve(q["question"], k=config.TOP_K)
    hit_sources = {h["source"] for h in hits}
    hit_pages = {(h["source"], h["page"]) for h in hits}

    answered = _really_answered(result, answer_text)
    return {
        "exact_doc_hit": q["expected_source"] in hit_sources,
        "exact_page_hit": (q["expected_source"], q["expected_page"]) in hit_pages,
        "answered": answered,
        "content_score": _overlap_ratio(q["expected_text"] or "", answer_text),
    }


def _compute_whole_document(q: dict) -> dict:
    """For the catch-up category (build_eval_questions.build_whole_document):
    documents with no usable heading at all can't be targeted by a specific
    section/page, only by "does the system find and discuss THIS document".
    No page-hit or content-overlap score -- there's no gold snippet to
    compare against, just whether the named document is what the system
    actually answers from."""
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)
    answered = _really_answered(result, answer_text)
    sources = result.sources if not isinstance(result, generation.ClarificationNeeded) else []
    return {
        "exact_doc_hit": q["expected_source"] in sources,
        "answered": answered,
    }


def _compute_procedural(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)

    hits = retrieval.retrieve(q["question"], k=config.TOP_K)
    hit_sources = {h["source"] for h in hits}
    hit_pages = {(h["source"], h["page"]) for h in hits}

    answered = _really_answered(result, answer_text)
    return {
        "exact_doc_hit": q["expected_source"] in hit_sources,
        "exact_page_hit": (q["expected_source"], q["expected_page"]) in hit_pages,
        "answered": answered,
        "content_score": _overlap_ratio(q["expected_text"] or "", answer_text),
    }


def _compute_cross_document(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)

    if isinstance(result, generation.ClarificationNeeded):
        returned_sources = set(result.candidates)
    else:
        returned_sources = set(result.sources)

    hits = retrieval.retrieve(q["question"], k=config.TOP_K)
    retrieved_sources = {h["source"] for h in hits}
    all_system_sources = returned_sources | retrieved_sources

    expected_sources = set(q["expected_sources"])
    overlap_sources = sorted(list(all_system_sources & expected_sources))

    answered = _really_answered(result, answer_text)
    hit = len(overlap_sources) > 0
    precision = len(overlap_sources) / len(all_system_sources) if all_system_sources else 0.0
    recall = len(overlap_sources) / len(expected_sources) if expected_sources else 0.0

    return {
        "hit": hit,
        "answered": answered,
        "overlap_count": len(overlap_sources),
        "expected_count": len(expected_sources),
        "precision": precision,
        "recall": recall,
        "overlap_sources": overlap_sources,
        "returned_sources": sorted(list(all_system_sources)),
    }


def _compute_refuse(q: dict) -> dict:
    result = generation.generate_answer(q["question"])
    answer_text = _answer_text(result)
    # A ClarificationNeeded also counts as correctly NOT hallucinating --
    # see _really_answered()'s docstring for the traced root cause.
    return {"refused": not _really_answered(result, answer_text)}


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

    answerable = [
        q for q in questions
        if not q["should_refuse"]
        and not q.get("is_enumerate")
        and not q.get("is_procedural")
        and not q.get("is_cross_doc")
        and not q.get("is_whole_document")
    ]
    procedural = [q for q in questions if q.get("is_procedural")]
    cross_doc = [q for q in questions if q.get("is_cross_doc")]
    enumerate_qs = [q for q in questions if q.get("is_enumerate")]
    refuse_qs = [q for q in questions if q["should_refuse"]]
    whole_doc_qs = [q for q in questions if q.get("is_whole_document")]

    total = len(answerable) + len(procedural) + len(cross_doc) + len(enumerate_qs) + len(refuse_qs) + len(whole_doc_qs)

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
    for i, q in enumerate(procedural):
        process(f"procedural:{i}", "procedural", q, _compute_procedural)
    for i, q in enumerate(cross_doc):
        process(f"cross_doc:{i}", "cross_document", q, _compute_cross_document)
    for i, q in enumerate(refuse_qs):
        process(f"refuse:{i}", "refuse", q, _compute_refuse)
    for i, q in enumerate(enumerate_qs):
        process(f"enumerate:{i}", "enumerate", q, _compute_enumerate)
    for i, q in enumerate(whole_doc_qs):
        process(f"whole_doc:{i}", "whole_document", q, _compute_whole_document)

    print(f"\nAll {total} questions processed in {time.time()-t_run_start:.1f}s this run.", flush=True)

    # ---- aggregate metrics ---- #
    tp = fn = tn = fp = 0
    tp_cases, fn_cases, tn_cases, fp_cases, failed_cases = [], [], [], [], []
    possible_classifier_misses = []

    # 1. Answerable metrics
    ans_doc_hits = ans_page_hits = 0
    ans_content_scores = []
    for i in range(len(answerable)):
        entry = checkpoint[f"answerable:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        if data["exact_doc_hit"]:
            ans_doc_hits += 1
        if data["exact_page_hit"]:
            ans_page_hits += 1
        if data["answered"]:
            tp += 1
            tp_cases.append(entry["question"])
        else:
            fn += 1
            fn_cases.append(entry["question"])
            if data["content_score"] >= 0.5:
                possible_classifier_misses.append((entry["question"], data["content_score"]))
        ans_content_scores.append(data["content_score"])

    # 2. Procedural metrics
    proc_doc_hits = proc_page_hits = 0
    proc_content_scores = []
    for i in range(len(procedural)):
        entry = checkpoint[f"procedural:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        if data["exact_doc_hit"]:
            proc_doc_hits += 1
        if data["exact_page_hit"]:
            proc_page_hits += 1
        if data["answered"]:
            tp += 1
            tp_cases.append(entry["question"])
        else:
            fn += 1
            fn_cases.append(entry["question"])
            if data["content_score"] >= 0.5:
                possible_classifier_misses.append((entry["question"], data["content_score"]))
        proc_content_scores.append(data["content_score"])

    # 2b. Whole-document metrics (catch-up category: documents with no usable
    # heading, targeted by a whole-document question instead of a specific
    # section). Folded into the SAME confusion matrix as Answerable/
    # Procedural -- unlike Cross-Document, this has a clean single expected
    # answer (the system should discuss this specific document), just no
    # page-level ground truth to also check.
    wd_doc_hits = 0
    for i in range(len(whole_doc_qs)):
        entry = checkpoint[f"whole_doc:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        if data["exact_doc_hit"]:
            wd_doc_hits += 1
        if data["answered"]:
            tp += 1
            tp_cases.append(entry["question"])
        else:
            fn += 1
            fn_cases.append(entry["question"])

    # 3. Cross-document metrics -- NOT folded into the overall TP/FP/FN/TN
    # confusion matrix. Root cause (found by tracing 4 real "FP" cases back to
    # their checkpoint data): ground truth here is "documents sharing an
    # incidental heading phrase" (a weak proxy for true topical relevance),
    # not a clean answerable/refuse label like the other categories. Real
    # evidence: 3 of 4 flagged cases were the system finding a MORE topically
    # relevant document than the narrow heading-based expected set (e.g. it
    # returned RIG_DOWN_RIG.PDF for "rigging down", which is arguably a
    # better answer than the 4 incidental MWD-tool docs listed as "expected").
    # Counting that as the same kind of FP as a should_refuse hallucination
    # silently changed what the headline Precision/FP numbers mean. This
    # category's own hit-rate/recall/precision (below) is the honest way to
    # report it -- see docs/EVALUATION_REPORT.md for the full writeup.
    cross_hits = 0
    cross_answered = 0
    cross_recalls = []
    cross_precisions = []
    for i in range(len(cross_doc)):
        entry = checkpoint[f"cross_doc:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        if data["hit"]:
            cross_hits += 1
        if data["answered"]:
            cross_answered += 1
        cross_recalls.append(data["recall"])
        cross_precisions.append(data["precision"])

    # 4. Should refuse metrics
    refuse_correct = 0
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

    # 5. Enumerate metrics
    enum_completeness_scores = []
    enum_exact_matches = 0
    for i in range(len(enumerate_qs)):
        entry = checkpoint[f"enumerate:{i}"]
        data = entry["data"]
        if "error" in data:
            failed_cases.append((entry["question"], data["error"]))
            continue
        enum_completeness_scores.append(data["completeness"])
        if data["exact_match"]:
            enum_exact_matches += 1

    n_ans = max(len(answerable), 1)
    n_proc = max(len(procedural), 1)
    n_cross = max(len(cross_doc), 1)
    n_ref = max(len(refuse_qs), 1)
    n_enum = max(len(enumerate_qs), 1)
    n_wd = max(len(whole_doc_qs), 1)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) else 0.0

    report = {
        "overall_confusion_matrix": {
            "TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "accuracy": round(accuracy, 3),
            "TP_cases": tp_cases, "FP_cases": fp_cases, "TN_cases": tn_cases, "FN_cases": fn_cases,
            "possible_classifier_misses": possible_classifier_misses,
        },
        "answerable_category": {
            "count": len(answerable),
            "exact_document_hit_rate": round(ans_doc_hits / n_ans, 3),
            "exact_page_hit_rate": round(ans_page_hits / n_ans, 3),
            "avg_content_overlap": round(sum(ans_content_scores) / len(ans_content_scores), 3) if ans_content_scores else 0.0,
        },
        "procedural_category": {
            "count": len(procedural),
            "exact_document_hit_rate": round(proc_doc_hits / n_proc, 3),
            "exact_page_hit_rate": round(proc_page_hits / n_proc, 3),
            "avg_content_overlap": round(sum(proc_content_scores) / len(proc_content_scores), 3) if proc_content_scores else 0.0,
        },
        "whole_document_category": {
            "count": len(whole_doc_qs),
            "exact_document_hit_rate": round(wd_doc_hits / n_wd, 3),
        },
        "cross_document_category": {
            "count": len(cross_doc),
            "hit_rate": round(cross_hits / n_cross, 3),
            "answered_rate": round(cross_answered / n_cross, 3),
            "avg_recall": round(sum(cross_recalls) / len(cross_recalls), 3) if cross_recalls else 0.0,
            "avg_precision": round(sum(cross_precisions) / len(cross_precisions), 3) if cross_precisions else 0.0,
        },
        "should_refuse_category": {
            "count": len(refuse_qs),
            "refuse_accuracy": round(refuse_correct / n_ref, 3),
        },
        "enumerate_category": {
            "count": len(enumerate_qs),
            "completeness_avg": round(sum(enum_completeness_scores) / len(enum_completeness_scores), 3) if enum_completeness_scores else 0.0,
            "exact_match_rate": round(enum_exact_matches / n_enum, 3),
        },
        "total_questions": total,
        "n_failed": len(failed_cases),
        "failed_cases": failed_cases,
    }
    return report


def print_confusion_matrix(cm: dict) -> None:
    print("\nConfusion matrix (positive = system answered rather than refused;")
    print("Answerable + Procedural + Should-refuse only -- Cross-Document and")
    print("Enumerate are scored separately, see their own category metrics):")
    print(f"                 Actual: answerable/procedural   Actual: should refuse")
    print(f"  Predicted answer      TP={cm['TP']:<10}      FP={cm['FP']:<10}")
    print(f"  Predicted refuse      FN={cm['FN']:<10}      TN={cm['TN']:<10}")
    print(f"\nPrecision={cm['precision']}  Recall={cm['recall']}  F1={cm['f1']}  Accuracy={cm['accuracy']}")
    if cm["FP_cases"]:
        print(f"\nFP ({len(cm['FP_cases'])} cases -- answered a question that should have been refused):")
        for q in cm["FP_cases"][:10]:
            print(f"  - {q}")
    if cm["FN_cases"]:
        print(f"\nFN ({len(cm['FN_cases'])} cases -- refused/missed when the corpus actually has the answer):")
        for q in cm["FN_cases"][:10]:
            print(f"  - {q}")


def write_report_md(report: dict) -> Path:
    cm = report["overall_confusion_matrix"]
    lines = [
        f"# RAG Evaluation Report",
        f"",
        f"**Generated:** {datetime.now().isoformat(timespec='seconds')}",
        f"**Total Questions:** {report['total_questions']}",
        f"",
        f"## Confusion Matrix (positive = system answered rather than refused)",
        f"",
        f"Covers Answerable + Procedural + Whole-Document + Should-refuse only.",
        f"Whole-Document (the corpus-coverage catch-up category) has the same clean",
        f"answer/refuse label as Answerable/Procedural, just no page-level ground",
        f"truth. Cross-Document and Enumerate don't fit a clean answer/refuse label",
        f"(see their own category metrics below) and are deliberately excluded from",
        f"this matrix -- see docs/EVALUATION_REPORT.md for why.",
        f"",
        f"| | Actual: Answerable / Procedural / Whole-Doc | Actual: Should Refuse |",
        f"|---|---|---|",
        f"| **Predicted Answer** | TP = {cm['TP']} | FP = {cm['FP']} |",
        f"| **Predicted Refuse** | FN = {cm['FN']} | TN = {cm['TN']} |",
        f"",
        f"- **Precision:** {cm['precision']}",
        f"- **Recall:** {cm['recall']}",
        f"- **F1 Score:** {cm['f1']}",
        f"- **Accuracy:** {cm['accuracy']}",
        f"",
        f"## Category Breakdown",
        f"",
        f"### 1. Answerable (Exact Heading Queries - {report['answerable_category']['count']} questions)",
        f"- **Exact Document Hit Rate:** {report['answerable_category']['exact_document_hit_rate']}",
        f"- **Exact Page Hit Rate:** {report['answerable_category']['exact_page_hit_rate']}",
        f"- **Avg Content Overlap:** {report['answerable_category']['avg_content_overlap']}",
        f"",
        f"### 2. Procedural / Instructional (Natural Language Queries - {report['procedural_category']['count']} questions)",
        f"- **Exact Document Hit Rate:** {report['procedural_category']['exact_document_hit_rate']}",
        f"- **Exact Page Hit Rate:** {report['procedural_category']['exact_page_hit_rate']}",
        f"- **Avg Content Overlap:** {report['procedural_category']['avg_content_overlap']}",
        f"",
        f"### 2b. Whole-Document (Corpus-Coverage Catch-Up - {report['whole_document_category']['count']} questions)",
        f"- **Exact Document Hit Rate:** {report['whole_document_category']['exact_document_hit_rate']}",
        f"",
        f"### 3. Cross-Document Discovery ({report['cross_document_category']['count']} questions)",
        f"- **Topic Discovery Hit Rate:** {report['cross_document_category']['hit_rate']}",
        f"- **Answered Rate:** {report['cross_document_category']['answered_rate']}",
        f"- **Avg Source Recall:** {report['cross_document_category']['avg_recall']}",
        f"- **Avg Source Precision:** {report['cross_document_category']['avg_precision']}",
        f"",
        f"### 4. Should Refuse (Off-topic & Fabricated - {report['should_refuse_category']['count']} questions)",
        f"- **Refusal Accuracy:** {report['should_refuse_category']['refuse_accuracy']}",
        f"",
        f"### 5. Enumerate ({report['enumerate_category']['count']} questions)",
        f"- **Completeness Avg:** {report['enumerate_category']['completeness_avg']}",
        f"- **Exact Match Rate:** {report['enumerate_category']['exact_match_rate']}",
        f"",
    ]

    if report.get("failed_cases"):
        lines.append("### Failed Questions (Errors)")
        for q, err in report["failed_cases"]:
            lines.append(f"- {q}: `{err}`")
        lines.append("")

    if cm["FP_cases"]:
        lines.append(f"### False Positives ({len(cm['FP_cases'])})")
        lines += [f"- {q}" for q in cm["FP_cases"]]
        lines.append("")

    if cm["FN_cases"]:
        lines.append(f"### False Negatives ({len(cm['FN_cases'])})")
        lines += [f"- {q}" for q in cm["FN_cases"]]
        lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH


if __name__ == "__main__":
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
        EVAL_PATH = config.DATA_DIR / f"eval_questions_{tag}.json"
        REPORT_PATH = config.DATA_DIR / f"eval_report_{tag}.md"
        CHECKPOINT_PATH = config.DATA_DIR / f"eval_checkpoint_{tag}.json"

    fresh = "--fresh" in sys.argv
    if fresh and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("--fresh: cleared existing checkpoint.", flush=True)

    report = run_evaluation(resume=not fresh)
    print_confusion_matrix(report["overall_confusion_matrix"])
    path = write_report_md(report)
    print(f"\nReport written to {path}")
