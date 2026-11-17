import hashlib
import json
import socket
import pytest
from app.retrieval_tradeoffs import DEV, HOLDOUT, PROTOCOL, run, score_filter, select_development


def test_frozen_first_evaluation_inputs():
    for path, digest in [
        (DEV, '80ca519ee3d47eff30c91dc992877c42f38b565aacdc33e7388a142cd86bdca3'),
        (HOLDOUT, '903d8de37b2c330c70cf505d0595c80fb68fc8894d2445c180c87d541edd2f41'),
        (PROTOCOL, 'f547ae00ab1f5b69d0f350513a2992abd84b7872281000ddf092c5c7b6841898')]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_full_experiment_preserves_unfavorable_tradeoff(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    result = run()
    assert result['selected_threshold'] == 0
    dev = result['splits']['development']['grid']
    held = result['splits']['holdout']['grid']
    assert dev[0]['recall_at_k'] == pytest.approx(10/11)
    assert dev[0]['no_answer_abstention_rate'] == pytest.approx(1/7)
    assert dev[2]['recall_at_k'] == pytest.approx(8/11)
    assert held[0]['recall_at_k'] == 1
    assert held[0]['no_answer_abstention_rate'] == pytest.approx(3/9)
    assert held[2]['recall_at_k'] == pytest.approx(5/11)
    assert held[2]['no_answer_abstention_rate'] == 1
    assert held[-1]['recall_at_k'] == 0  # perfect refusal is not successful retrieval
    assert result['selected_holdout'] == held[0]
    assert result['observed_modes'][1]['effective_modes'] == ['lexical']
    assert result['human_semantic_review'] == 'not_reviewed'


def test_selection_does_not_reward_abstention_by_sacrificing_recall():
    rows = [dict(threshold=0, recall_at_k=.8, no_answer_abstention_rate=.2),
            dict(threshold=.25, recall_at_k=.8, no_answer_abstention_rate=.4),
            dict(threshold=.5, recall_at_k=.7, no_answer_abstention_rate=1),
            dict(threshold=.75, recall_at_k=.8, no_answer_abstention_rate=.4)]
    assert select_development(rows) == .25


def test_filter_ignores_relevance_labels():
    row = dict(id='q',question='alpha beta',tags=[],returned_snapshot_ids=['s'],expected_snapshot_ids=['s'])
    report = {'results':[{'details':[row]}]}
    data = {'documents':[dict(id='s',title='alpha',text='unrelated')]}
    before = score_filter(report, data, .5)['details'][0]['returned_snapshot_ids']
    row['expected_snapshot_ids'] = []
    after = score_filter(report, data, .5)['details'][0]['returned_snapshot_ids']
    assert before == after == ['s']
    assert score_filter(report, data, .75)['details'][0]['returned_snapshot_ids'] == []


def test_holdout_labels_cannot_change_selected_threshold(tmp_path):
    data = json.loads(HOLDOUT.read_text())
    for case in data['cases']:
        case['relevant_snapshot_ids'] = []
    path = tmp_path/'holdout.json'
    path.write_text(json.dumps(data))
    result = run(holdout_path=path)
    assert result['selected_threshold'] == 0
    assert result['selected_holdout']['recall_at_k'] is None


@pytest.mark.parametrize('field,records', [('id','cases'),('question','cases'),('id','documents'),('article','documents'),('text','documents')])
def test_detect_split_contamination(tmp_path, field, records):
    dev, held = json.loads(DEV.read_text()), json.loads(HOLDOUT.read_text())
    # For ID changes preserve foreign references so contamination is the only error.
    if records == 'documents' and field == 'id':
        old = held[records][0][field]
        for case in held['cases']:
            case['relevant_snapshot_ids'] = [dev[records][0][field] if x == old else x for x in case['relevant_snapshot_ids']]
        held['historical_snapshot_ids'] = [dev[records][0][field] if x == old else x for x in held['historical_snapshot_ids']]
    held[records][0][field] = dev[records][0][field]
    path = tmp_path/'held.json'
    path.write_text(json.dumps(held))
    with pytest.raises(ValueError, match='Split overlap'):
        run(holdout_path=path)
