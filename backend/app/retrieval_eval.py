"""Offline retrieval metrics for frozen question -> relevant snapshot fixtures."""
from __future__ import annotations


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")
    found = set(ranked_ids[:k]) & relevant_ids
    return len(found) / len(relevant_ids)


def reciprocal_rank(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")
    for rank, item_id in enumerate(ranked_ids, start=1):
        if item_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def evaluate_cases(cases: list[dict], search, *, k: int = 20) -> dict:
    if not cases:
        raise ValueError("cases must not be empty")
    recalls, rrs = [], []
    details = []
    for case in cases:
        relevant = set(case["relevant_snapshot_ids"])
        ranked = list(search(case["question"]))
        recall = recall_at_k(ranked, relevant, k)
        rr = reciprocal_rank(ranked, relevant)
        recalls.append(recall); rrs.append(rr)
        details.append({
            "id": case["id"],
            "recall_at_k": recall,
            "reciprocal_rank": rr,
            "returned": len(ranked),
        })
    return {
        "cases": len(cases),
        "k": k,
        "recall_at_k": sum(recalls) / len(recalls),
        "mrr": sum(rrs) / len(rrs),
        "details": details,
    }
