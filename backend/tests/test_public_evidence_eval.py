import hashlib
import json
import socket
import pytest
from app.public_evidence_eval import DEFAULT_DATASET,load_public_dataset,run
from app.research_eval import load_benchmark


def test_public_sources_are_not_synthetic_or_live_provider_results(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('benchmark must not access network')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    report=run()
    assert report['provenance']=='public_excerpts'
    assert report['excerpt_count']==3 and report['stored_excerpt_words']==43
    assert report['human_review_completed'] is False
    assert report['real_embedding']['status']=='not_run'
    for result in report['results']:
        assert result['recall_at_k']==pytest.approx(4/6)
        assert result['mrr_at_k']==pytest.approx(3.5/6)
        assert result['no_answer_abstention_rate']==pytest.approx(1/3)
        assert result['exact_quote_rate']==1 and result['scope_violations']==0
        by_id={d['id']:d for d in result['details']}
        assert not by_id['pub-q4']['returned_snapshot_ids']
        assert not by_id['pub-q6']['returned_snapshot_ids']
        assert by_id['pub-q7']['returned_snapshot_ids']  # false positive retained
        for detail in result['details']:
            for cite in detail['citations']:
                assert cite['url'].startswith('https://') and 'example.invalid' not in cite['url']
    assert all(d['degraded_reason']=='vector_database_unavailable' for d in report['results'][1]['details'])


def test_default_synthetic_loader_does_not_silently_accept_public_text():
    with pytest.raises(ValueError,match='provenance'):
        load_benchmark(DEFAULT_DATASET)


@pytest.mark.parametrize('mutation', ['hash','rights','copyright','long','duplicate','host','credential','locator','human','mixed'])
def test_public_ingestion_provenance_fails_closed(tmp_path,mutation):
    data=json.loads(DEFAULT_DATASET.read_text()); d=data['documents'][0]
    if mutation=='hash':d['text']+=' changed'
    elif mutation=='rights':d['rights']['policy_url']=''
    elif mutation=='copyright':d['rights']['copyrighted_third_party_material']=True
    elif mutation=='long':d['text']='word '*26
    elif mutation=='duplicate':data['documents'][1]['source_url']=d['source_url']
    elif mutation=='host':d['source_url']='https://unvetted.invalid/source'
    elif mutation=='credential':d['source_url']='https://user:secret@www.nist.gov/source'
    elif mutation=='locator':d['source_locator']=''
    elif mutation=='human':data['label_origin']='human_reviewed'
    elif mutation=='mixed':data['provenance']='synthetic'
    path=tmp_path/'bad.json';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_public_dataset(path)


def test_exact_excerpt_hashes_and_citation_sources():
    data=load_public_dataset(DEFAULT_DATASET)
    for document in data['documents']:
        assert document['excerpt_sha256']==hashlib.sha256(document['text'].encode()).hexdigest()
        assert document['source_attribution'].endswith('(NIST)')
        assert document['rights']['review_kind'].startswith('AI-assisted')
