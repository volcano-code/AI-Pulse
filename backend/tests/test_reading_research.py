"""v0.3 tests use explicit synthetic fixtures. No provider request is made."""
import json
import time
from types import SimpleNamespace
import pytest
from sqlalchemy import select, update
from app.models import Article, Snapshot, Workspace, Investigation
from app.reading import compare_text
from app.research import execute_research
from app.research_model import ResearchModelClient, request_turn
from app.schemas import ResearchRequest
from app.llm import ModelError
from app.textutil import digest
from app.timeutil import iso, utcnow
import httpx


def first(client):
    return client.get('/api/v1/articles').json()[0]


def change(client, item, text='SYNTHETIC: New sentence about agent tool budgets. 这是新增的测试内容。'):
    with client.app.state.session_factory.begin() as db:
        article = db.get(Article, item['id'])
        snap = Snapshot(article_id=article.id, title=article.title, text=text, content_hash=digest(text))
        db.add(snap);db.flush();article.current_snapshot_id=snap.id
        return snap.id


def test_read_marker_is_exact_version_and_reversible(seeded):
    a = first(seeded)
    assert seeded.get(f"/api/v1/articles/{a['id']}/changes").json()['status'] == 'unread'
    assert seeded.put(f"/api/v1/articles/{a['id']}/read", json={'snapshot_id': a['snapshot_id']}).status_code == 200
    assert seeded.get(f"/api/v1/articles/{a['id']}/changes").json()['status'] == 'read'
    new = change(seeded, a)
    delta = seeded.get(f"/api/v1/articles/{a['id']}/changes").json()
    assert delta['status'] == 'changed_since_read' and delta['target_snapshot_id'] == new
    assert any(c['kind'] == 'added' for c in delta['changes'])
    assert seeded.get(f"/api/v1/articles/{a['id']}/changes?target_snapshot_id={a['snapshot_id']}").json()['status'] == 'read'
    assert seeded.delete(f"/api/v1/articles/{a['id']}/read",headers={'Content-Type':'application/json'}).status_code == 200
    assert seeded.get(f"/api/v1/articles/{a['id']}/changes").json()['status'] == 'unread'


def test_read_marker_cannot_point_to_other_article(seeded):
    a,b = seeded.get('/api/v1/articles').json()[:2]
    assert seeded.put(f"/api/v1/articles/{a['id']}/read",json={'snapshot_id':b['snapshot_id']}).status_code == 422
    assert seeded.get(f"/api/v1/articles/{a['id']}/changes?target_snapshot_id={b['snapshot_id']}").status_code == 422


@pytest.mark.parametrize('path,method,body',[
    ('/articles/missing/read','put',{'snapshot_id':'missing'}),
    ('/articles/missing/read','delete',None),
    ('/articles/missing/changes','get',None),
    ('/investigations/missing','get',None),
])
def test_missing_resources_404(client,path,method,body):
    assert client.request(method,'/api/v1'+path,json=body,headers={'Content-Type':'application/json'}).status_code == 404


@pytest.mark.parametrize('before,after',[
    ('相同😀。','相同😀。'), ('a\nb','a\nc'), ('旧资料。第二句！','新资料。第二句！'),
    ('','新文本'),('删除的内容',''),('😀版本一。','😀版本二。'),
])
def test_diff_offsets_are_exact_unicode_slices(before,after):
    result=compare_text(before,after)
    for part in result['changes']:
        text = after if part['kind']=='added' else before
        assert text[part['start']:part['end']]==part['text']
    if before == after:
        assert not result['changes']


def test_diff_is_bounded():
    assert compare_text('a'*40_000, 'b'*40_000)['truncated']
    result = compare_text('旧。'*100,'新。'*100)
    assert len(result['changes']) <= 80 and result['truncated']


def test_updates_exclude_disabled_source(seeded):
    a = first(seeded)
    seeded.patch('/api/v1/sources/'+a['source_id'],json={'enabled':False})
    rows=seeded.get('/api/v1/updates').json()
    assert all(r['source_id']!=a['source_id'] for r in rows)


def test_brief_keeps_its_original_read_target(seeded):
    seeded.post('/api/v1/runs',json={'kind':'brief'})
    brief = seeded.get('/api/v1/briefs/latest').json()
    item=brief['items'][0]
    seeded.put(f"/api/v1/articles/{item['article_id']}/read",json={'snapshot_id':item['snapshot_id']})
    change(seeded, {'id':item['article_id']})
    loaded=seeded.get('/api/v1/briefs/'+brief['id']).json()['items'][0]
    assert loaded['snapshot_id']==item['snapshot_id'] and loaded['reading']['status']=='read'


def test_research_extractive_persists_trace_and_quotes(seeded):
    response=seeded.post('/api/v1/investigations',json={'question':'Agent 工作流'})
    assert response.status_code==200
    data=response.json();result=data['result']
    assert data['status']=='completed' and result['model_calls']==0
    assert result['usage']['cost_usd']==0 and result['citations']
    assert result['retrieval']['requested_mode']=='lexical'
    assert result['retrieval']['effective_mode']=='lexical'
    assert result['retrieval']['degraded'] is False
    assert {'start','tool','finish'} <= {e['stage'] for e in data['trace']}
    assert seeded.get('/api/v1/investigations/'+data['id']).json()['result']==result
    for c in result['citations']:
        snap=seeded.get('/api/v1/snapshots/'+c['snapshot_id']).json()
        assert snap['text'][c['quote_start']:c['quote_end']]==c['quote']


def test_research_idempotency_and_conflict(seeded):
    body={'question':'Agent','idempotency_key':'fixed-request'}
    one=seeded.post('/api/v1/investigations',json=body).json()
    two=seeded.post('/api/v1/investigations',json=body).json()
    assert one['id']==two['id'] and two['cached']
    assert seeded.post('/api/v1/investigations',json=body|{'question':'Other'}).status_code==409
    assert len(seeded.get('/api/v1/investigations').json())==1


def test_research_abstains_for_unknown_query(seeded):
    data=seeded.post('/api/v1/investigations',json={'question':'qzxunmatchedkeyword'}).json()
    assert data['result']['stop_reason']=='insufficient_evidence'
    assert data['result']['citations']==[] and data['result']['claims']==[]


@pytest.mark.parametrize('body',[
    {'question':' '},{'question':'x'},{'question':'x'*401},
    {'question':'Agent','max_tool_calls':0},{'question':'Agent','max_tool_calls':5},
    {'question':'Agent','max_model_calls':5},{'question':'Agent','url':'https://attacker.invalid'},
    {'question':'Agent','unexpected':'value'},
])
def test_research_request_validation(client,body):
    assert client.post('/api/v1/investigations',json=body).status_code==422


def test_research_unknown_brief_is_not_silently_ignored(seeded):
    assert seeded.post('/api/v1/investigations',json={'question':'Agent','brief_id':'missing'}).status_code==404


def test_new_routes_are_authenticated(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(settings.model_copy(update={'admin_token':'a'*32}))) as c:
        for path in ['/updates','/investigations','/articles/any/changes']:
            assert c.get('/api/v1'+path).status_code==401
        assert c.post('/api/v1/investigations',json={'question':'Agent'}).status_code==401


class FakeModel:
    """Explicit scripted provider double, not a real LLM."""
    def __init__(self,turns):self.turns=iter(turns);self.calls=0
    def next_turn(self,messages,timeout):
        self.calls+=1
        value=next(self.turns)
        if isinstance(value,Exception):raise value
        return {'message':{'role':'assistant',**value},'usage':{'prompt_tokens':20,'completion_tokens':10}}


def tool_call(name='search_saved_evidence',args=None,id='call1'):
    return {'tool_calls':[{'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args if args is not None else {'query':'Agent'})}}]}


def final(ids=None):
    return {'content':json.dumps({'claims':[{'text':'测试资料讨论 Agent 工作流。','evidence_ids':ids or ['E1']}],'insufficient_evidence':False},ensure_ascii=False)}


@pytest.fixture
def live_contract(seeded):
    factory=seeded.app.state.session_factory
    with factory.begin() as db:
        db.execute(update(Article).values(data_mode='live'))
        db.get(Workspace,1).data_mode='live'
    settings=seeded.app.state.settings.model_copy(update={'data_mode':'live','llm_mode':'live','llm_api_key':'TEST-SECRET-NOT-REAL','llm_model':'synthetic-contract-model'})
    return factory,settings


def test_tool_agent_selects_search_inspect_and_cited_draft(live_contract):
    factory,settings=live_contract
    model=FakeModel([tool_call(),tool_call('inspect_evidence',{'evidence_id':'E1'},'call2'),final()])
    data=execute_research(factory,settings,ResearchRequest(question='Agent'),model)
    assert data['status']=='needs_review'
    assert data['result']['model_calls']==3 and data['result']['tool_calls']==2
    assert data['result']['claims'][0]['evidence_ids']==['E1']
    assert data['result']['usage']['prompt_tokens']==60
    assert data['result']['usage']['cost_usd'] is None
    assert 'TEST-SECRET' not in json.dumps(data)


@pytest.mark.parametrize('turns',[
    [tool_call('run_shell',{'command':'echo bad'})],
    [tool_call('inspect_evidence',{'evidence_id':'E9'})],
    [tool_call(args={'query':'Agent','url':'http://127.0.0.1'})],
    [tool_call(args={'query':'x'*401})],
    [tool_call(),final(['E99'])],
    [final()],
    [{'content':'not json'}],
    [ModelError('Upstream TEST-SECRET')],
    [tool_call(),tool_call(id='call1')],
    [{'content':json.dumps({'claims':[],'insufficient_evidence':False})}],
])
def test_agent_rejects_invalid_tools_evidence_and_responses(live_contract,turns):
    factory,settings=live_contract
    data=execute_research(factory,settings,ResearchRequest(question='Agent'),FakeModel(turns))
    assert data['status']=='needs_attention' and data['result']['claims']==[]
    assert 'TEST-SECRET' not in json.dumps(data)


def test_agent_respects_model_budget(live_contract):
    factory,settings=live_contract
    data=execute_research(factory,settings,ResearchRequest(question='Agent',max_model_calls=1),FakeModel([tool_call()]))
    assert data['status']=='partial' and data['result']['model_calls']==1
    assert data['result']['stop_reason']=='model_budget_exceeded'


def test_agent_respects_tool_budget(live_contract):
    factory,settings=live_contract
    data=execute_research(factory,settings,ResearchRequest(question='Agent',max_tool_calls=1),FakeModel([tool_call(),tool_call(id='second')]))
    assert data['status']=='partial' and data['result']['tool_calls']==1
    assert data['result']['claims']==[]


def test_provider_contract_sends_actual_tools_and_hides_reasoning(live_contract):
    _,settings=live_contract
    def handler(request):
        payload=json.loads(request.content)
        assert {t['function']['name'] for t in payload['tools']}=={'search_saved_evidence','inspect_evidence'}
        assert payload['response_format']=={'type':'json_object'}
        return httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':tool_call()|{'reasoning_content':'not returned'}}],'usage':{}})
    client=ResearchModelClient(settings,transport=httpx.MockTransport(handler))
    result=client.next_turn([{'role':'user','content':'Agent'}],timeout=3)
    assert 'reasoning_content' not in result['message'] and result['message']['tool_calls']


@pytest.mark.parametrize('base',['http://localhost','https://user:password@server.test','https://server.test?key=secret'])
def test_provider_rejects_untrusted_base(base):
    with pytest.raises(ModelError):
        request_turn(base,'x','x',[],100,1)


def test_model_process_no_secret_argv_env(live_contract,monkeypatch):
    from app import research_model
    _,settings=live_contract
    observed={}
    def run(cmd,**kwargs):
        observed.update({'cmd':cmd,**kwargs})
        return SimpleNamespace(returncode=0,stdout=b'{"message":{"role":"assistant","content":"{}"},"usage":{}}')
    monkeypatch.setattr(research_model.subprocess,'run',run)
    ResearchModelClient(settings).next_turn([],timeout=3)
    assert settings.llm_api_key not in str(observed['cmd'])
    assert settings.llm_api_key not in str(observed['env'])
    assert settings.llm_api_key in observed['input'].decode()


def test_incomplete_research_is_not_replayed_on_restart(seeded):
    from app.research import recover_interrupted_research
    factory=seeded.app.state.session_factory
    body=ResearchRequest(question='Agent',idempotency_key='interrupted')
    completed=execute_research(factory,seeded.app.state.settings,body)
    with factory.begin() as db:
        row=db.get(Investigation,completed['id'])
        row.status='running';row.finished_at=None;row.result={}
        row.trace=[{'stage':'model_request','message':'request started','at':iso(utcnow())}]
    recover_interrupted_research(factory)
    cached=execute_research(factory,seeded.app.state.settings,body)
    assert cached['cached'] and cached['status']=='needs_attention'
    assert cached['result']['stop_reason']=='interrupted_before_result'
    assert cached['result']['model_calls']==1
    assert cached['result']['usage']['cost_usd'] is None
    assert cached['trace'][-1]['stage']=='interrupted'


def test_research_stops_on_elapsed_budget_without_a_model_call(live_contract):
    factory,settings=live_contract
    times=iter([0,settings.research_deadline_seconds+1,settings.research_deadline_seconds+2])
    model=FakeModel([])
    data=execute_research(factory,settings,ResearchRequest(question='Agent'),model,clock=lambda:next(times))
    assert data['status']=='partial' and data['result']['stop_reason']=='budget_exceeded'
    assert model.calls==0


def test_excess_parallel_tool_batch_is_rejected_before_execution(live_contract):
    factory,settings=live_contract
    call=tool_call();call['tool_calls']+=tool_call(id='call2')['tool_calls']
    data=execute_research(factory,settings,ResearchRequest(question='Agent',max_tool_calls=1),FakeModel([call]))
    assert data['status']=='partial' and data['result']['tool_calls']==0


def test_historical_research_does_not_read_new_snapshot(seeded):
    seeded.post('/api/v1/runs',json={'kind':'brief'})
    brief=seeded.get('/api/v1/briefs/latest').json()
    item=next(x for x in brief['items'] if '工作流' in x['title'])
    new=change(seeded,{'id':item['article_id']},'SYNTHETIC Agent: exclusivelynewword')
    result=seeded.post('/api/v1/investigations',json={'question':'Agent 工作流','brief_id':brief['id']}).json()['result']
    assert all(c['snapshot_id']!=new for c in result['citations'])
    assert any(c['snapshot_id']==item['snapshot_id'] for c in result['citations'])



def test_research_hybrid_mode_degrades_explicitly_without_vector_database(seeded):
    factory=seeded.app.state.session_factory
    settings=seeded.app.state.settings.model_copy(update={
        'retrieval_mode':'hybrid',
        'embedding_provider':'fixture',
        'embedding_model':'fixture-sha256-v1',
        'embedding_dim':8,
    })
    data=execute_research(factory,settings,ResearchRequest(question='Agent 工作流'))
    receipt=data['result']['retrieval']
    assert receipt['requested_mode']=='hybrid'
    assert receipt['effective_mode']=='lexical'
    assert receipt['degraded'] is True
    assert receipt['degraded_reason']=='vector_database_unavailable'
    tool_events=[x for x in data['trace'] if x['stage']=='tool' and x.get('tool')=='search_saved_evidence']
    assert tool_events and tool_events[0]['retrieval']==receipt


def test_research_request_identity_includes_retrieval_configuration(seeded):
    factory=seeded.app.state.session_factory
    body=ResearchRequest(question='Agent',idempotency_key='retrieval-identity')
    lexical=execute_research(factory,seeded.app.state.settings,body)
    assert lexical['status']=='completed'
    hybrid=seeded.app.state.settings.model_copy(update={
        'retrieval_mode':'hybrid',
        'embedding_provider':'fixture',
        'embedding_model':'fixture-sha256-v1',
        'embedding_dim':8,
    })
    with pytest.raises(Exception) as exc:
        execute_research(factory,hybrid,body)
    assert 'different research request' in str(exc.value)
