import pytest
from app.retrieval_eval import assert_thresholds, evaluate_cases, recall_at_k, reciprocal_rank


@pytest.mark.parametrize('metric', ['recall_at_k', 'mrr'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -0.1, 1.1, True])
def test_invalid_metrics_cannot_pass_quality_gate(metric, value):
    result = {'k': 5, 'recall_at_k': 1.0, 'mrr': 1.0, metric: value}
    with pytest.raises(ValueError):
        assert_thresholds(result, min_recall=0.8, min_mrr=0.8)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -0.1, 1.1, True])
def test_thresholds_must_be_finite_probabilities(value):
    with pytest.raises(ValueError):
        assert_thresholds({'k': 5, 'recall_at_k': 1.0, 'mrr': 1.0}, min_recall=value, min_mrr=0.8)
    with pytest.raises(ValueError):
        assert_thresholds({'k': 5, 'recall_at_k': 1.0, 'mrr': 1.0}, min_recall=0.8, min_mrr=value)


@pytest.mark.parametrize('k', [True, 0, -1, 1.5])
def test_k_requires_positive_integer(k):
    with pytest.raises(ValueError):
        recall_at_k(['a'], {'a'}, k)
    with pytest.raises(ValueError):
        evaluate_cases([{'id': 'one', 'question': 'q', 'relevant_snapshot_ids': ['a']}], lambda _: ['a'], k=k)


def test_duplicate_case_ids_rejected_before_search():
    case = {'id': 'one', 'question': 'q', 'relevant_snapshot_ids': ['a']}
    def search(_):
        pytest.fail('Invalid evaluation set must be rejected before search')
    with pytest.raises(ValueError, match='duplicate'):
        evaluate_cases([case, dict(case)], search)


def test_duplicate_results_do_not_inflate_recall_or_shift_rank_up():
    assert recall_at_k(['a', 'a', 'b'], {'a', 'b'}, 2) == 0.5
    assert reciprocal_rank(['x', 'x', 'a'], {'a'}) == pytest.approx(1 / 3)


def test_empty_results_are_zero_but_empty_labels_are_not_answerable_cases():
    case = {'id': 'one', 'question': 'q', 'relevant_snapshot_ids': ['a']}
    result = evaluate_cases([case], lambda _: [])
    assert result['recall_at_k'] == result['mrr'] == 0.0
    with pytest.raises(ValueError):
        evaluate_cases([], lambda _: [])
    with pytest.raises(ValueError):
        evaluate_cases([{**case, 'relevant_snapshot_ids': []}], lambda _: [])
