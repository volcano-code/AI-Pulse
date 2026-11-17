import hashlib
import json
import socket
import pytest
from app.numeric_retrieval_eval import DEV,HOLDOUT,PROTOCOL,accepts,numeric_atoms,run,select_development


def test_frozen_inputs_and_protocol():
    for path,digest in [(DEV,'01d21875f72369f82e30f96a54da05b02b4e885c6f47364e688b066464c8e1a4'),
                        (HOLDOUT,'84087fe46ccc5dce1cf16d49fd4151574cc79837c7041e4bc4c7bc54217cb25b'),
                        (PROTOCOL,'5c17e2439000967ad10fcae4965fe1debba0cd3203b3752619bf5580d3c57cac')]:
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest


def test_numeric_identity_is_deliberately_not_semantic_normalization():
    assert numeric_atoms('v2.4 2026-03-04 第十二项') == {'2.4','2026','03','04'}
    assert not accepts('每分钟12次','每分钟十二次','require_all')
    assert not accepts('不是30次吗','限额20次','reject_disjoint')
    assert accepts('3.0的价格','无版本信息','reject_disjoint')
    assert not accepts('3.0的价格','无版本信息','require_all')
    with pytest.raises(ValueError):
        accepts('q','d','other')


def test_frozen_offline_outcome_preserves_losses(monkeypatch):
    def forbidden(*a,**kw):
        raise AssertionError('no network allowed')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    result=run()
    assert result['selected_policy']=='baseline'
    dev=result['splits']['development']['results']
    held=result['splits']['holdout']['results']
    assert dev[0]['recall_at_k']==1
    assert dev[1]['recall_at_k']==pytest.approx(9.5/12)
    assert dev[2]['recall_at_k']==pytest.approx(8.5/12)
    assert held[0]['no_answer_abstention_rate']==0
    assert held[1]['recall_at_k']==1 and held[1]['no_answer_abstention_rate']==0
    assert held[2]['recall_at_k']==pytest.approx(14/17)
    assert held[2]['no_answer_abstention_rate']==pytest.approx(3/9)
    assert len(held[2]['answerable_cases_losing_recall'])==3
    assert result['selected_holdout']==held[0]
    assert result['human_semantic_review']=='not_reviewed'
    assert held[2]['overlapping_tag_slices']


def test_selection_rejects_case_level_harm_even_if_macro_unchanged():
    base=dict(policy='baseline',recall_at_k=.5,no_answer_abstention_rate=.2,answerable_cases_losing_recall=[])
    harm=dict(policy='reject_disjoint',recall_at_k=.5,no_answer_abstention_rate=.9,answerable_cases_losing_recall=['lost'])
    good=dict(policy='require_all',recall_at_k=.5,no_answer_abstention_rate=.4,answerable_cases_losing_recall=[])
    assert select_development([base,harm])=='baseline'
    assert select_development([base,harm,good])=='require_all'


def test_holdout_labels_do_not_select_policy(tmp_path):
    data=json.loads(HOLDOUT.read_text())
    for case in data['cases']:
        case['relevant_snapshot_ids']=[]
    path=tmp_path/'held.json'; path.write_text(json.dumps(data))
    result=run(holdout_path=path)
    assert result['selected_policy']=='baseline'
    assert result['selected_holdout']['recall_at_k'] is None


def test_split_contamination_rejected(tmp_path):
    data=json.loads(HOLDOUT.read_text())
    data['cases'][0]['question']=json.loads(DEV.read_text())['cases'][0]['question']
    path=tmp_path/'held.json'; path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Split overlap'):
        run(holdout_path=path)


def test_no_numeric_question_keeps_all_candidates():
    for policy in ['baseline','reject_disjoint','require_all']:
        assert accepts('工具支持离线吗','版本2.4离线部署',policy)


def test_label_free_policy_filters_only_using_text():
    assert accepts('版本3.0','版本2.4','baseline')
    assert not accepts('版本3.0','版本2.4','reject_disjoint')
    assert accepts('比较版本2.4和3.0','版本2.4','reject_disjoint')
    assert not accepts('比较版本2.4和3.0','版本2.4','require_all')
