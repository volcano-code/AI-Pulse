from pathlib import Path
import pytest
from app.retrieval_eval import (
    assert_thresholds, evaluate_cases, evaluate_lexical_dataset,
    load_dataset, recall_at_k, reciprocal_rank,
)

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_eval_synthetic_v1.json"


def test_retrieval_metrics_are_exact():
    assert recall_at_k(["a","b","c"], {"b","d"}, 2) == 0.5
    assert recall_at_k(["a","b","d"], {"b","d"}, 3) == 1.0
    assert reciprocal_rank(["x","b","d"], {"b","d"}) == 0.5
    assert reciprocal_rank(["x"], {"b"}) == 0.0


def test_eval_aggregates_frozen_cases():
    cases=[
        {"id":"q1","question":"agent","relevant_snapshot_ids":["s2"]},
        {"id":"q2","question":"llm","relevant_snapshot_ids":["s3","s4"]},
    ]
    rankings={"agent":["s1","s2"],"llm":["s4","s9","s3"]}
    result=evaluate_cases(cases,lambda q:rankings[q],k=2)
    assert result["recall_at_k"]==pytest.approx(0.75)
    assert result["mrr"]==pytest.approx(0.75)


def test_synthetic_dataset_is_valid_and_meets_regression_gate(seeded):
    dataset=load_dataset(FIXTURE)
    factory=seeded.app.state.session_factory
    with factory() as db:
        result=evaluate_lexical_dataset(db,dataset,data_mode="replay",k=20)
    assert result["synthetic"] is True
    assert result["cases"] == 10
    assert_thresholds(result,min_recall=0.90,min_mrr=0.80)


def test_dataset_rejects_duplicate_case_ids(tmp_path):
    p=tmp_path/"bad.json"
    p.write_text('{"version":"x","cases":[{"id":"q","question":"a","relevant_urls":["u"]},{"id":"q","question":"b","relevant_urls":["v"]}]}')
    with pytest.raises(ValueError):
        load_dataset(p)


def test_threshold_failure_is_actionable():
    with pytest.raises(AssertionError,match="Recall"):
        assert_thresholds({"k":20,"recall_at_k":0.5,"mrr":1.0},min_recall=0.8,min_mrr=0.6)
