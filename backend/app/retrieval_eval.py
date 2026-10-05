"""Offline retrieval evaluation for frozen question -> evidence fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from sqlalchemy import select
from .models import Article
from .retrieval import build_scope, lexical_candidates


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")
    return len(set(ranked_ids[:k]) & relevant_ids) / len(relevant_ids)


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
    recalls, rrs, details = [], [], []
    for case in cases:
        relevant = set(case["relevant_snapshot_ids"])
        ranked = list(search(case["question"]))
        recall, rr = recall_at_k(ranked, relevant, k), reciprocal_rank(ranked, relevant)
        recalls.append(recall); rrs.append(rr)
        details.append({"id": case["id"], "recall_at_k": recall, "reciprocal_rank": rr, "returned": len(ranked)})
    return {"cases": len(cases), "k": k, "recall_at_k": sum(recalls)/len(recalls),
            "mrr": sum(rrs)/len(rrs), "details": details}


def load_dataset(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("version") or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("Invalid retrieval dataset")
    seen = set()
    for case in data["cases"]:
        cid, question, urls = case.get("id"), case.get("question"), case.get("relevant_urls")
        if not cid or cid in seen or not isinstance(question, str) or not question.strip():
            raise ValueError("Invalid or duplicate retrieval case")
        if not isinstance(urls, list) or not urls or len(urls) != len(set(urls)):
            raise ValueError("Each case requires unique relevant_urls")
        seen.add(cid)
    return data


def resolve_cases(db, dataset: dict, *, data_mode: str) -> list[dict]:
    urls = {url for case in dataset["cases"] for url in case["relevant_urls"]}
    rows = db.execute(select(Article.canonical_url, Article.current_snapshot_id).where(
        Article.canonical_url.in_(urls), Article.data_mode == data_mode
    )).all()
    mapping = {url: snapshot_id for url, snapshot_id in rows if snapshot_id}
    missing = sorted(urls - mapping.keys())
    if missing:
        raise ValueError(f"Dataset references missing URLs: {missing}")
    return [{"id": case["id"], "question": case["question"],
             "relevant_snapshot_ids": [mapping[url] for url in case["relevant_urls"]]}
            for case in dataset["cases"]]


def evaluate_lexical_dataset(db, dataset: dict, *, data_mode: str, k: int = 20) -> dict:
    cases = resolve_cases(db, dataset, data_mode=data_mode)
    scope = build_scope(db, None, data_mode)
    def search(question: str):
        return [row["snapshot_id"] for row in lexical_candidates(db, question, scope, limit=min(k, 100))]
    return evaluate_cases(cases, search, k=k) | {
        "dataset_version": dataset["version"], "mode": "lexical",
        "synthetic": dataset["version"].startswith("synthetic-"),
    }


def assert_thresholds(result: dict, *, min_recall: float, min_mrr: float) -> None:
    if result["recall_at_k"] < min_recall:
        raise AssertionError(f"Recall@{result['k']} {result['recall_at_k']:.3f} < {min_recall:.3f}")
    if result["mrr"] < min_mrr:
        raise AssertionError(f"MRR {result['mrr']:.3f} < {min_mrr:.3f}")
