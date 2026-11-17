import json
import socket
import pytest
from app.research_eval import DEFAULT_DATASET, evaluate, load_benchmark


@pytest.fixture
def report(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('offline benchmark attempted network')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    return evaluate()


def test_synthetic_baseline_records_failures_not_fake_success(report):
    assert report['provenance'] == 'synthetic'
    assert report['real_embedding']['status'] == 'not_run'
    assert report['human_semantic_review']['support_rate'] is None
    for row in report['results']:
        assert (row['cases'], row['answerable_cases'], row['no_answer_cases']) == (12, 7, 5)
        assert row['recall_at_k'] == pytest.approx(6/7)
        assert row['mrr_at_k'] == pytest.approx(6/7)
        assert row['no_answer_abstention_rate'] == pytest.approx(3/5)
        assert row['exact_quote_rate'] == 1
        assert row['scope_violations'] == 0
        details = {d['id']: d for d in row['details']}
        assert details['historical']['returned_snapshot_ids'] == ['old']
        assert details['current']['returned_snapshot_ids'] == ['new']
        assert details['cross_language']['recall_at_k'] == 0
        assert not details['overlap_unanswerable']['retrieval_abstained']
        assert details['overlap_unanswerable']['recall_at_k'] is None
        assert all(d['human_semantic_review']['status'] == 'not_reviewed' for d in row['details'])


def test_hybrid_reports_actual_fallback_and_no_embedding_calls(report):
    lexical, hybrid = report['results']
    for a,b in zip(lexical['details'], hybrid['details']):
        assert a['returned_snapshot_ids'] == b['returned_snapshot_ids']
        assert a['effective_mode'] == b['effective_mode'] == 'lexical'
        assert not a['degraded'] and b['degraded']
        assert b['degraded_reason'] == 'vector_database_unavailable'
        assert b['query_embedding']['embedding_calls'] == 0


@pytest.mark.parametrize('k', [0, 6, True, 1.5])
def test_invalid_k_rejected(k):
    with pytest.raises(ValueError):
        evaluate(k=k)


def test_deterministic_results_except_observed_timing(report):
    other = evaluate()
    for result in [report, other]:
        for mode in result['results']:
            for detail in mode['details']:
                detail.pop('latency_ms')
    assert report == other


@pytest.mark.parametrize('mutation', ['real', 'duplicate', 'unknown'])
def test_dataset_provenance_and_identity_fail_closed(tmp_path, mutation):
    data = json.loads(DEFAULT_DATASET.read_text())
    if mutation == 'real':
        data['provenance'] = 'real'
    elif mutation == 'duplicate':
        data['cases'].append(data['cases'][0])
    else:
        data['cases'][0]['relevant_snapshot_ids'] = ['missing']
    path = tmp_path/'fixture.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_benchmark(path)


def test_independent_scope_oracle_detects_production_scope_regression(monkeypatch):
    # Simulate production scope accidentally including a disabled source. Stub
    # retrieval too because lexical's second gate otherwise masks the regression.
    monkeypatch.setattr('app.research_eval.search_research_evidence', lambda *a, **k: {
        'citations': [{'snapshot_id':'disabled','quote':'隐藏标记秘密能力。','quote_start':0,'quote_end':9}],
        'effective_mode':'lexical','degraded':False,'degraded_reason':None,
        'query_embedding':{'embedding_calls':0}})
    assert all(row['scope_violations'] == 12 for row in evaluate()['results'])


def test_historical_scope_bug_is_detected_independently(monkeypatch):
    from app.retrieval import RetrievalScope
    from app.models import Snapshot
    from sqlalchemy import select
    def broken_scope(db, brief, mode):
        return RetrievalScope(tuple(db.scalars(select(Snapshot.id))), brief, mode)
    monkeypatch.setattr('app.retrieval.build_scope', broken_scope)
    monkeypatch.setattr('app.research_retrieval.build_scope', broken_scope)
    for mode in evaluate()['results']:
        row = next(d for d in mode['details'] if d['id'] == 'historical')
        assert 'new' in row['returned_snapshot_ids']
        assert row['scope_violations'] == 1
