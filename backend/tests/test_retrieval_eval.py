import pytest
from app.retrieval_eval import evaluate_cases, recall_at_k, reciprocal_rank


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
    assert result["cases"]==2
    assert result["recall_at_k"]==pytest.approx(0.75)
    assert result["mrr"]==pytest.approx(0.75)


@pytest.mark.parametrize("call",[
    lambda: recall_at_k(["a"],set(),1),
    lambda: recall_at_k(["a"],{"a"},0),
    lambda: reciprocal_rank(["a"],set()),
    lambda: evaluate_cases([],lambda q:[],k=20),
])
def test_eval_rejects_invalid_fixtures(call):
    with pytest.raises(ValueError):
        call()
