from copy import deepcopy
import hashlib
import json
import socket
import pytest
from app.annotation_review import (DEFAULT_REVIEW,FIXTURES,apply_review,draft_review,run,
                                   sufficient_set_covered,validate_review)


def inputs():
    entry=json.loads(DEFAULT_REVIEW.read_text())['reviews'][0]
    raw=(FIXTURES/'research_eval_development_v4.json').read_bytes()
    return json.loads(raw),raw,entry


def test_overlay_is_versioned_and_does_not_mutate_original():
    data,raw,entry=inputs(); original=deepcopy(data)
    revised=apply_review(data,raw,entry)
    assert data==original and json.loads(raw)==data
    before=next(c for c in data['cases'] if c['id']=='n-dev-11')
    after=next(c for c in revised['cases'] if c['id']=='n-dev-11')
    assert before['relevant_snapshot_ids']==[]
    assert after['original_annotation']==before
    assert after['relevant_snapshot_ids']==['n-dev-lake']
    assert 'no_answer' not in after['tags'] and 'answerable' in after['tags']
    assert revised['version']!=data['version']
    assert 'not_human' in revised['label_origin']


@pytest.mark.parametrize('mutation,match', [
    ('hash','hash'),('original','Original'),('question','Original'),('unknown','case ID'),
    ('duplicate','case ID'),('human','human'),('status','AI-provisional'),
    ('negative','Quote'),('bool','offsets'),('quote','Quote'),('scope','scope'),
    ('missing_evidence','traceable'),('empty_group','evidence set'),('nonminimal','minimal'),
    ('reason','rationale'),('falsification','rationale')])
def test_invalid_review_fails_closed(mutation,match):
    data,raw,entry=inputs(); d=entry['decisions'][0]
    if mutation=='hash':entry['source_sha256']='wrong'
    elif mutation=='original':d['original_relevant_snapshot_ids']=['n-dev-lake']
    elif mutation=='question':d['question']='another'
    elif mutation=='unknown':d['case_id']='unknown'
    elif mutation=='duplicate':entry['decisions'].append(deepcopy(d))
    elif mutation=='human':entry['human_review_completed']=True
    elif mutation=='status':entry['status']='human_confirmed'
    elif mutation=='negative':d['evidence'][0]['start']=-1
    elif mutation=='bool':d['evidence'][0]['start']=False
    elif mutation=='quote':d['evidence'][0]['quote']='made up'
    elif mutation=='scope':d['proposed_relevant_snapshot_ids']=['n-dev-silver-old']
    elif mutation=='missing_evidence':d['evidence']=[]
    elif mutation=='empty_group':d['sufficient_evidence_sets']=[[]]
    elif mutation=='nonminimal':d['sufficient_evidence_sets']*=2
    elif mutation=='reason':d['reason']=''
    elif mutation=='falsification':d['falsification_condition']=''
    with pytest.raises(ValueError,match=match):
        apply_review(data,raw,entry)


def test_draft_requires_explicit_completed_evidence():
    data,raw,_=inputs()
    draft=draft_review(data,raw,['n-dev-11'],'new-version')
    assert draft['status']=='draft'
    with pytest.raises(ValueError):validate_review(data,raw,draft)
    with pytest.raises(ValueError):draft_review(data,raw,['missing'],'new-version')


def test_alternative_sufficient_sets_are_not_recall_all():
    assert sufficient_set_covered(['direct'],[['direct'],['a','b']]) is True
    assert sufficient_set_covered(['a'],[['direct'],['a','b']]) is False
    assert sufficient_set_covered(['a','b'],[['direct'],['a','b']]) is True
    assert sufficient_set_covered([],[]) is None


def test_run_is_offline_and_old_hashes_remain_unchanged(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('network not permitted')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    paths=[FIXTURES/'research_eval_development_v4.json',FIXTURES/'research_eval_holdout_v4.json']
    hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    report=run()
    assert report['status']=='ai_provisional' and not report['human_review_completed']
    dev,held=report['comparisons']
    assert dev['results'][0]['original_metrics']['answerable_cases']==12
    assert dev['results'][0]['provisional_metrics']['answerable_cases']==13
    assert held['results'][0]['original_metrics']['mrr_at_k']==pytest.approx(16.5/17)
    assert held['results'][0]['provisional_metrics']['mrr_at_k']==1
    assert all(c['sufficient_set_coverage_proxy'] for r in report['comparisons'] for c in r['results'][0]['changes'])
    assert hashes==[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


def test_duplicate_manifest_rejected(tmp_path):
    manifest=json.loads(DEFAULT_REVIEW.read_text())
    manifest['reviews'].append(manifest['reviews'][0])
    p=tmp_path/'duplicate.json';p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Duplicate source'):
        run(p)


@pytest.mark.parametrize('mutation',['text','labels'])
def test_in_memory_data_cannot_bypass_frozen_hash(mutation):
    data,raw,entry=inputs()
    if mutation=='text':
        document=next(d for d in data['documents'] if d['id']=='n-dev-lake')
        document['text']='替换原文'
        entry['decisions'][0]['evidence'][0].update(quote='替换原文',start=0,end=4)
    else:
        case=next(c for c in data['cases'] if c['id']=='n-dev-11')
        case['relevant_snapshot_ids']=['n-dev-lake']
        entry['decisions'][0]['original_relevant_snapshot_ids']=['n-dev-lake']
    with pytest.raises(ValueError,match='source bytes'):
        validate_review(data,raw,entry)
