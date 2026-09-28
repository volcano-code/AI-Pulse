import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings


def test_health(client):
    assert client.get("/health").json()["status"]=="ok"


def test_no_secret_leaks(client):
    value=client.get("/api/v1/status").text
    assert "llm_api_key" not in value and "admin_token" not in value


def test_latest_empty(client):
    assert client.get("/api/v1/briefs/latest").status_code==404


def test_replay_network_rejected(client):
    assert client.post("/api/v1/runs",json={"kind":"ingest"}).status_code==409


def test_generation_via_api(seeded):
    r=seeded.post("/api/v1/runs",json={"kind":"brief"})
    assert r.status_code==202
    run=seeded.get("/api/v1/runs/"+r.json()["run_id"]).json()
    assert run["status"]=="succeeded"
    brief=seeded.get("/api/v1/briefs/latest").json()
    assert len(brief["items"])==6 and brief["data_mode"]=="replay"
    export=seeded.get(f"/api/v1/briefs/{brief['id']}/markdown")
    assert export.status_code==200 and "REPLAY" in export.text


def test_bookmark_persistence(seeded):
    article=seeded.get("/api/v1/articles").json()[0]
    assert seeded.patch(f"/api/v1/articles/{article['id']}/bookmark",json={"bookmarked":True}).status_code==200
    saved=seeded.get("/api/v1/articles?bookmarked=true").json()
    assert len(saved)==1 and saved[0]["id"]==article["id"]


def test_source_switch(client):
    assert client.patch("/api/v1/sources/arxiv",json={"enabled":False}).status_code==200
    assert not next(x for x in client.get("/api/v1/sources").json() if x["id"]=="arxiv")["enabled"]


def test_preferences_saved(client):
    data={"timezone":"Asia/Shanghai","topics":["Agent"],"blocked_keywords":["ads"],"max_items":4,"lookback_hours":24}
    assert client.put("/api/v1/preferences",json=data).json()==data
    assert client.get("/api/v1/preferences").json()==data


def test_sse_real_protocol(seeded):
    r=seeded.post("/api/v1/ask/stream",json={"question":"Agent 工作流"})
    assert r.status_code==200 and r.headers["content-type"].startswith("text/event-stream")
    assert "event: retrieval\n" in r.text and "event: citation\n" in r.text and "event: done\n" in r.text


@pytest.mark.parametrize("url",["/api/v1/snapshots/missing","/api/v1/briefs/missing","/api/v1/runs/missing"])
def test_not_found(client,url):
    assert client.get(url).status_code==404


def test_bad_origin(client):
    assert client.post("/api/v1/runs",json={"kind":"brief"},headers={"Origin":"https://evil.example"}).status_code==403


def test_bad_content_type(client):
    assert client.post("/api/v1/runs",content='{"kind":"brief"}',headers={"Content-Type":"text/plain"}).status_code==415


def test_unknown_host(client):
    assert client.get("/health",headers={"Host":"evil.example"}).status_code==400


def test_admin_auth(tmp_path):
    token="a"*32
    app=create_app(Settings(_env_file=None,database_url=f"sqlite:///{tmp_path/'auth.db'}",admin_token=token))
    with TestClient(app) as client:
        assert client.get("/api/v1/status").status_code==401
        assert client.get("/api/v1/status",headers={"Authorization":"Bearer wrong"}).status_code==401
        assert client.get("/api/v1/status",headers={"Authorization":f"Bearer {token}"}).status_code==200
        assert client.get("/health").status_code==200


def test_ask_missing_edition(client):
    assert client.post("/api/v1/ask",json={"question":"Agent","brief_id":"unknown"}).status_code==404


def test_bearer_scheme_is_required(tmp_path):
    token="b"*32
    app=create_app(Settings(_env_file=None,database_url=f"sqlite:///{tmp_path/'scheme.db'}",admin_token=token))
    with TestClient(app) as client:
        assert client.get("/api/v1/status",headers={"Authorization":token}).status_code==401


def test_title_search_uses_bound_parameters(seeded):
    r=seeded.get("/api/v1/articles",params={"q":"' OR 1=1 --"})
    assert r.status_code==200 and r.json()==[]
    assert seeded.get("/api/v1/status").json()["articles"]==6


def test_oversized_question_rejected(client):
    assert client.post("/api/v1/ask",json={"question":"x"*401}).status_code==422
