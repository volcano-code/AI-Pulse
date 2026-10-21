"""Offline contracts. No paid provider, real query text or mail is transmitted."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import event, func, select
from app.embedding_gateway import EmbeddingBatch, EmbeddingGateway, FixtureEmbeddingProvider
from app.models import ProviderCall, ProviderCallResource, ProviderReconciliation, QueryEmbedding
from app.provider_calls import CallLedger, CallBlocked, CallConflict, fingerprint
from app.provider_reconciliation import inspect_call, reconcile
from app.query_embeddings import embed_saved_query, QueryEmbeddingUnavailable


class Spy(FixtureEmbeddingProvider):
    def __init__(self, failure=None):
        super().__init__(8)
        self.calls, self.failure = 0, failure
    def embed(self, texts, *, purpose):
        self.calls += 1
        assert purpose == "query"
        if self.failure:
            raise self.failure
        b = super().embed(texts, purpose=purpose)
        return EmbeddingBatch(b.vectors, b.provider, b.model, b.dimensions, input_tokens=13, request_id="req-fixture")


@pytest.fixture
def setup(seeded):
    f = seeded.app.state.session_factory
    s = seeded.app.state.settings.model_copy(update={"embedding_provider": "fixture",
        "embedding_model": "fixture-sha256-v1", "embedding_dim": 8, "retrieval_mode": "hybrid"})
    return f, s


def decision(factory, call_id, **extra):
    return reconcile(factory, call_id=call_id,
        expected_fingerprint=inspect_call(factory, call_id)["expected_fingerprint"],
        decision="authorize_retry", operator_billing="unknown", evidence_sha256=fingerprint("offline ticket"),
        actor="fixture-operator", apply=True, confirm_call_id=call_id,
        workers_stopped=True, acknowledge_possible_charge=True, **extra)["decision"]["id"]


def uncertain(factory, kind="query_embedding", resource_keys=None):
    c = CallLedger(factory).reserve(operation_key="operation", request_hash=fingerprint("payload"),
        kind=kind, provider="fixture", model="m", data_mode="replay", resource_keys=resource_keys)
    c.unknown(elapsed_ms=2)
    return c


def test_query_success_cached_without_raw_query_in_ledger(setup):
    f,s = setup; p=Spy(); g=EmbeddingGateway(p)
    v,a=embed_saved_query(f,s,"fixture private query",g)
    vv,b=embed_saved_query(f,s,"fixture private query",g)
    assert v==vv and p.calls==1 and a["embedding_calls"]==1 and b["embedding_calls"]==0
    assert b["reused"] and b["input_tokens"]==0 and b["receipt"]["input_tokens"]==13
    assert "fixture private query" not in json.dumps(CallLedger(f).inspect())
    with f() as db:
        assert db.scalar(select(func.count()).select_from(QueryEmbedding))==1


@pytest.mark.parametrize("query",["", " ", "a", "x"*401])
def test_invalid_query_never_dispatches(setup,query):
    f,s=setup; p=Spy()
    with pytest.raises(ValueError): embed_saved_query(f,s,query,EmbeddingGateway(p))
    assert p.calls==0


def test_query_timeout_blocked_until_explicit_ticket(setup):
    f,s=setup; p=Spy(TimeoutError("DO-NOT-LOG-SECRET")); g=EmbeddingGateway(p)
    with pytest.raises(QueryEmbeddingUnavailable) as failure: embed_saved_query(f,s,"agent evidence",g)
    assert "SECRET" not in str(failure.value)
    cid=failure.value.call["call_id"]
    p.failure=None
    with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"agent evidence",g)
    ticket=decision(f,cid)
    # Approval alone never changes the ordinary automatic execution path.
    with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"agent evidence",g)
    assert p.calls==1
    _,a=embed_saved_query(f,s,"agent evidence",g,retry_authorization=ticket)
    _,b=embed_saved_query(f,s,"agent evidence",g,retry_authorization=ticket)
    _,c=embed_saved_query(f,s,"agent evidence",g)
    assert p.calls==2 and a["receipt"]["status"]=="succeeded" and b["reused"] and c["reused"]
    with f() as db:
        assert db.get(ProviderCall,cid).status=="unknown"
        rec=db.scalar(select(ProviderReconciliation).where(ProviderReconciliation.call_id==cid))
        assert rec.replacement_call_id==a["receipt"]["call_id"]


def test_ticket_cannot_authorize_different_query(setup):
    f,s=setup; p=Spy(TimeoutError());g=EmbeddingGateway(p)
    with pytest.raises(QueryEmbeddingUnavailable) as e: embed_saved_query(f,s,"original query",g)
    ticket=decision(f,e.value.call["call_id"])
    with pytest.raises(CallConflict): embed_saved_query(f,s,"changed query",g,retry_authorization=ticket)
    assert p.calls==1


def test_query_atomic_output_receipt_rollback_stays_blocked(setup):
    f,s=setup;p=Spy();g=EmbeddingGateway(p)
    def fail(*a): raise RuntimeError("injected write error")
    event.listen(QueryEmbedding,"before_insert",fail)
    try:
        with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"atomic query",g)
    finally: event.remove(QueryEmbedding,"before_insert",fail)
    with f() as db:
        assert db.scalar(select(func.count()).select_from(QueryEmbedding))==0
        r=db.scalar(select(ProviderCall))
        assert r.status=="unknown" and r.input_tokens==13
    with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"atomic query",g)
    assert p.calls==1


def test_concurrent_query_has_one_dispatch(setup):
    f,s=setup;entered,finish=Event(),Event()
    class Slow(Spy):
        def embed(self,texts,*,purpose):
            entered.set();assert finish.wait(5)
            return super().embed(texts,purpose=purpose)
    p=Slow();g=EmbeddingGateway(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(embed_saved_query,f,s,"concurrent query",g)
        assert entered.wait(5)
        try:
            with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"concurrent query",g)
        finally: finish.set()
        assert future.result()[1]["receipt"]["status"]=="succeeded"
    assert p.calls==1


def test_cache_corruption_does_not_trigger_rebilling(setup):
    f,s=setup;p=Spy();g=EmbeddingGateway(p)
    _,a=embed_saved_query(f,s,"protected cache",g)
    with f.begin() as db: db.get(QueryEmbedding,a["receipt"]["call_id"]).vector=[0.0]*8
    with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"protected cache",g)
    assert p.calls==1


def test_non_fixture_requires_opt_in_before_network(setup):
    f,s=setup
    class Paid(Spy): provider="openai"
    p=Paid();s=s.model_copy(update={"embedding_provider":"openai"})
    with pytest.raises(QueryEmbeddingUnavailable,match="not_authorized"):
        embed_saved_query(f,s,"do not send",EmbeddingGateway(p))
    assert p.calls==0


def test_reconcile_dry_run_is_read_only_and_requires_acknowledgments(setup):
    f,_=setup;c=uncertain(f);info=inspect_call(f,c.id)
    kwargs=dict(call_id=c.id,expected_fingerprint=info["expected_fingerprint"],decision="authorize_retry",
        operator_billing="unknown",evidence_sha256=fingerprint("test evidence"),actor="test")
    assert reconcile(f,**kwargs)["status"]=="dry_run"
    with f() as db: assert db.scalar(select(func.count()).select_from(ProviderReconciliation))==0
    with pytest.raises(ValueError): reconcile(f,**kwargs,apply=True)
    with pytest.raises(ValueError): reconcile(f,**kwargs,apply=True,confirm_call_id=c.id,workers_stopped=True)


def test_stale_receipt_preview_rejected(setup):
    f,_=setup;c=CallLedger(f).reserve(operation_key="operation",request_hash=fingerprint("payload"),
        kind="query_embedding",provider="fixture",model="m",data_mode="replay")
    before=inspect_call(f,c.id)["expected_fingerprint"]
    c.observe(request_id="req-new")
    with pytest.raises(CallConflict): reconcile(f,call_id=c.id,expected_fingerprint=before,
        decision="keep_blocked",operator_billing="unknown",evidence_sha256=fingerprint("evidence"),actor="tester")


def test_inflight_freeze_fences_late_success(setup):
    f,_=setup;c=CallLedger(f).reserve(operation_key="operation",request_hash=fingerprint("payload"),
        kind="query_embedding",provider="fixture",model="m",data_mode="replay")
    decision(f,c.id)
    with f.begin() as db:
        with pytest.raises(CallBlocked): c.succeed(db,elapsed_ms=2,summary={"embedded":1})
    assert c.read()["status"]=="unknown"


def test_decisions_immutable_and_success_or_research_not_retryable(setup):
    f,_=setup;c=uncertain(f,kind="research_turn")
    with pytest.raises(ValueError,match="Research"): decision(f,c.id)
    kwargs=dict(call_id=c.id,expected_fingerprint=inspect_call(f,c.id)["expected_fingerprint"],
        decision="keep_blocked",operator_billing="charged",evidence_sha256=fingerprint("evidence"),actor="op",apply=True,
        confirm_call_id=c.id,workers_stopped=True)
    x=reconcile(f,**kwargs);assert reconcile(f,**kwargs)["reused"]
    with pytest.raises(CallConflict): reconcile(f,**(kwargs|{"operator_billing":"not_charged"}))
    assert x["decision"]["provider_verified"] is False


def test_concurrent_ticket_consumption_and_resource_transfer(setup):
    f,_=setup;keys=[fingerprint("resource")];c=uncertain(f,kind="embedding_backfill",resource_keys=keys)
    ticket=decision(f,c.id)
    def reserve():
        return CallLedger(f).reserve(operation_key="operation",request_hash=fingerprint("payload"),
            kind="embedding_backfill",provider="fixture",model="m",data_mode="replay",resource_keys=keys,
            retry_authorization=ticket)
    with ThreadPoolExecutor(max_workers=2) as pool: a,b=list(pool.map(lambda _:reserve(),range(2)))
    assert a.id==b.id and sorted([a.reused,b.reused])==[False,True]
    with f() as db:
        assert db.get(ProviderCallResource,keys[0]).call_id==a.id
        assert db.get(ProviderCall,c.id).status=="unknown"
        assert db.scalar(select(func.count()).select_from(ProviderCall))==2


def test_changed_batch_cannot_consume_ticket(setup):
    f,_=setup;c=uncertain(f,kind="embedding_backfill",resource_keys=[fingerprint("one")]);ticket=decision(f,c.id)
    with pytest.raises(CallConflict): CallLedger(f).reserve(operation_key="other-batch",request_hash=fingerprint("payload"),
        kind="embedding_backfill",provider="fixture",model="m",data_mode="replay",retry_authorization=ticket)


def test_query_invalid_vector_keeps_reported_usage(setup):
    f,s=setup
    class Bad(Spy):
        def embed(self,texts,*,purpose):
            self.calls+=1
            return EmbeddingBatch([[float("nan")]*8],self.provider,self.model,8,input_tokens=9)
    p=Bad()
    with pytest.raises(QueryEmbeddingUnavailable): embed_saved_query(f,s,"bad response",EmbeddingGateway(p))
    with f() as db:
        r=db.scalar(select(ProviderCall));assert r.status=="unknown" and r.input_tokens==9


def test_cli_does_not_mutate_without_apply(setup,monkeypatch,capsys):
    from scripts import provider_reconcile as cli
    f,s=setup;c=uncertain(f)
    monkeypatch.setattr(cli,"Settings",lambda:s)
    assert cli.main(["--call-id",c.id])==0
    assert json.loads(capsys.readouterr().out)["status"]=="read_only"
    assert cli.main(["--call-id",c.id,"--apply"])==1
    with f() as db: assert db.scalar(select(func.count()).select_from(ProviderReconciliation))==0


def test_brief_retry_requires_ticket_and_keeps_human_review(setup):
    from types import SimpleNamespace
    from app.audited_models import draft_brief_item
    from app.models import Snapshot, BriefModelDraft
    f,s=setup
    with f() as db: snap=db.scalars(select(Snapshot)).first()
    class Model:
        calls=0;failure=True
        def draft(self,title,text):
            self.calls+=1
            if self.failure: raise TimeoutError("fixture failure")
            return SimpleNamespace(title=title,summary="SYNTHETIC fixture",evidence_quote=text[:40]),{}
    m=Model()
    with pytest.raises(TimeoutError): draft_brief_item(f,s,snap.id,snap.title,snap.text,model_client=m)
    with f() as db: cid=db.scalar(select(ProviderCall.id))
    ticket=decision(f,cid);m.failure=False
    with pytest.raises(CallBlocked): draft_brief_item(f,s,snap.id,snap.title,snap.text,model_client=m)
    _,a=draft_brief_item(f,s,snap.id,snap.title,snap.text,model_client=m,retry_authorization=ticket)
    _,b=draft_brief_item(f,s,snap.id,snap.title,snap.text,model_client=m,retry_authorization=ticket)
    assert m.calls==2 and b["model_calls"]==0
    with f() as db:
        assert db.get(ProviderCall,cid).status=="unknown"
        assert db.get(ProviderCall,a["provider_call_id"]).summary["draft_requires_review"]
        assert db.get(BriefModelDraft,a["provider_call_id"])


def test_backfill_retry_is_explicit_and_single_original_batch(setup):
    from app.durable_backfill import durable_backfill
    from app.embeddings import ensure_snapshot_chunks
    from app.models import Snapshot
    f,s=setup
    with f.begin() as db:
        for snap in db.scalars(select(Snapshot)): ensure_snapshot_chunks(db,snap)
    class Provider(FixtureEmbeddingProvider):
        calls=0;failure=True
        def embed(self,texts,*,purpose):
            self.calls+=1
            if self.failure: raise TimeoutError("fixture")
            return super().embed(texts,purpose=purpose)
    p=Provider(8);g=EmbeddingGateway(p)
    a=durable_backfill(f,g,data_mode="replay",max_chunks=2,batch_size=2)
    ticket=decision(f,a["calls"][0]["call_id"]);p.failure=False
    with pytest.raises(ValueError): durable_backfill(f,g,data_mode="replay",max_chunks=4,batch_size=2,retry_authorization=ticket)
    assert durable_backfill(f,g,data_mode="replay",max_chunks=2,batch_size=2)["status"]=="needs_attention"
    b=durable_backfill(f,g,data_mode="replay",max_chunks=2,batch_size=2,retry_authorization=ticket)
    assert b["embedded"]==2 and p.calls==2
    # The consumed authorization cannot silently move on to another pending batch.
    c=durable_backfill(f,g,data_mode="replay",max_chunks=2,batch_size=2,retry_authorization=ticket)
    assert c["status"]=="needs_attention" and p.calls==2


def test_replacement_reservation_rollback_does_not_consume_authorization(setup):
    f,_=setup;c=uncertain(f);ticket=decision(f,c.id)
    def fail(*args): raise RuntimeError("write failure")
    event.listen(ProviderCall,"before_insert",fail)
    try:
        with pytest.raises(RuntimeError): CallLedger(f).reserve(operation_key="operation",request_hash=fingerprint("payload"),
            kind="query_embedding",provider="fixture",model="m",data_mode="replay",retry_authorization=ticket)
    finally: event.remove(ProviderCall,"before_insert",fail)
    with f() as db:
        item=db.get(ProviderReconciliation,ticket)
        assert item.replacement_call_id is None
        assert db.scalar(select(func.count()).select_from(ProviderCall))==1


@pytest.mark.parametrize("calls", [1, None])
def test_research_does_not_report_zero_cost_for_embedding_dispatch(setup,monkeypatch,calls):
    from app import research
    from app.schemas import ResearchRequest
    f,s=setup;s=s.model_copy(update={"retrieval_mode":"lexical"})
    original=research.search_research_evidence
    def fake_receipt(db,settings,query,brief_id):
        return original(db,settings,query,brief_id)|{"query_embedding":{
            "embedding_calls":calls,"input_tokens":13 if calls else None,"status":"fixture_contract"}}
    monkeypatch.setattr(research,"search_research_evidence",fake_receipt)
    record=research.execute_research(f,s,ResearchRequest(question="Agent"))
    usage=record["result"]["usage"]
    assert record["result"]["model_calls"]==0
    assert usage["embedding_calls"]==calls and usage["cost_usd"] is None
    assert usage["embedding_input_tokens"]==(13 if calls else None)
