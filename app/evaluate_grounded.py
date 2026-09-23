"""Benchmark harness: runs the grounded eval questions (data/eval_questions.json)
against the live system and scores retrieval / behavioral / content-correctness
metrics. Scaled-down for this laptop's small test corpus (Section 10) --
not the 600-file benchmark from Section 9, which is reference-only here.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import config, database, generation, retrieval

EVAL_PATH = config.DATA_DIR / "eval_questions.json"


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

        if not generation.looks_like_refusal(answer_text):
            behavioral_correct += 1

        content_scores.append(_overlap_ratio(q["expected_text"] or "", answer_text))

    refuse_correct = 0
    for q in refuse_qs:
        result = generation.generate_answer(q["question"])
        answer_text = result.message if isinstance(result, generation.ClarificationNeeded) else result.text
        if generation.looks_like_refusal(answer_text):
            refuse_correct += 1

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
    return report


if __name__ == "__main__":
    report = run_evaluation()
    print(json.dumps(report, indent=2))
