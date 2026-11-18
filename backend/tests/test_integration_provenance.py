import importlib.util
import json
from pathlib import Path
import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.db import Base
from app.models import BriefItem
from app.public_evidence_eval import DEFAULT_DATASET,load_public_dataset
from app.research_eval import seed

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('verify_integration',ROOT/'scripts/verify_integration.py')
verification=importlib.util.module_from_spec(spec);spec.loader.exec_module(verification)


def test_public_historical_brief_keeps_original_source_provenance():
    data=load_public_dataset(DEFAULT_DATASET)
    data['historical_snapshot_ids']=['pub-launch']
    engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            seed(db,data)
            item=db.scalar(select(BriefItem))
            original=next(d for d in data['documents'] if d['id']=='pub-launch')
            assert item.source_url==original['source_url']
            assert item.source_name==original['source_attribution']
            assert item.evidence_quote==original['text']
    finally:engine.dispose()


def test_valid_manifest_runs_read_only_ancestry_checks(monkeypatch):
    manifest=json.loads(verification.MANIFEST.read_text())
    commands=[]
    def git_check(args, **kwargs):
        commands.append(args)
        assert args[:3]==['git','merge-base','--is-ancestor']
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(verification.subprocess,'run',git_check)
    monkeypatch.setattr(verification.subprocess,'check_output',lambda *a,**k: manifest['stack_tip']+'\n')
    assert verification.verify(manifest)['included_pull_requests']==list(range(24,36))
    assert len(commands)==25
    # Actual full-history verification is an explicit CI gate, not a unit-test prerequisite.


@pytest.mark.parametrize('mutation', ['missing','link','tip','invalid_sha'])
def test_manifest_rejects_missing_or_rewired_stack(mutation):
    manifest=json.loads(verification.MANIFEST.read_text())
    if mutation=='missing':manifest['pull_requests'].pop()
    elif mutation=='link':manifest['pull_requests'][1]['base_sha']='a'*40
    elif mutation=='tip':manifest['stack_tip']='b'*40
    else:
        manifest['main_base']='--bad'
        manifest['pull_requests'][0]['base_sha']='--bad'
    with pytest.raises(ValueError):verification.verify(manifest)


def test_missing_ancestor_fails_closed(monkeypatch):
    manifest=json.loads(verification.MANIFEST.read_text())
    monkeypatch.setattr(verification.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1))
    with pytest.raises(ValueError,match='missing from HEAD ancestry'):
        verification.verify(manifest)
