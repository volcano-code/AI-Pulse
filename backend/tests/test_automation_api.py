from pathlib import Path
import pytest
from app.briefing import build_brief
from app.models import Brief, Delivery
from app.worker import step
from test_durable import durable_client, env


def test_automation_status_is_explicit_and_no_credentials(durable_client):
    response = durable_client.get('/api/v1/automation')
    assert response.status_code == 200
    body = response.json()
    assert body['task_mode'] == 'durable' and body['delivery_transport'] == 'file'
    assert body['schedule']['enabled'] is False and body['worker_online'] is False
    assert 'password' not in response.text and 'api_key' not in response.text


def test_save_schedule_and_read_back(durable_client):
    data = {'enabled': True, 'local_time': '09:15', 'timezone': 'Asia/Shanghai', 'send': True}
    response = durable_client.put('/api/v1/automation/schedule', json=data)
    assert response.status_code == 200
    actual = durable_client.get('/api/v1/automation').json()['schedule']
    assert all(actual[k] == v for k, v in data.items())


@pytest.mark.parametrize('value', [{'local_time':'25:00'}, {'local_time':'9:00'}, {'timezone':'Not/AZone'}, {'timezone':'../../etc/passwd'}])
def test_invalid_schedule_is_rejected(durable_client, value):
    assert durable_client.put('/api/v1/automation/schedule', json=value).status_code == 422


def test_inline_mode_cannot_claim_automatic_scheduling(client):
    assert client.put('/api/v1/automation/schedule', json={'enabled':True}).status_code == 409
    assert client.post('/api/v1/automation/run', json={}).status_code == 409


def test_daily_submit_is_idempotent_and_worker_processes(durable_client):
    f, s = env(durable_client)
    data = {'send':True, 'idempotency_key':'daily-click-1'}
    one = durable_client.post('/api/v1/automation/run', json=data)
    two = durable_client.post('/api/v1/automation/run', json=data)
    assert one.status_code == two.status_code == 202
    assert one.json()['run_id'] == two.json()['run_id']
    step(f, s)
    deliveries = durable_client.get('/api/v1/deliveries').json()
    assert len(deliveries) == 1 and deliveries[0]['status'] == 'file_written'
    preview = durable_client.get(f"/api/v1/deliveries/{deliveries[0]['id']}/preview")
    assert preview.status_code == 200 and 'message/rfc822' in preview.headers['content-type']
    assert 'SYNTHETIC' in preview.text


def test_same_key_with_different_send_flag_conflicts(durable_client):
    durable_client.post('/api/v1/automation/run', json={'send':False, 'idempotency_key':'one'})
    result = durable_client.post('/api/v1/automation/run', json={'send':True, 'idempotency_key':'one'})
    assert result.status_code == 409


def test_manual_delivery_requires_explicit_confirmation(durable_client):
    f, s = env(durable_client)
    brief = build_brief(f, s)
    path = f"/api/v1/briefs/{brief['brief_id']}/deliver"
    assert durable_client.post(path, json={}).status_code == 422
    one = durable_client.post(path, json={'confirmed':True})
    two = durable_client.post(path, json={'confirmed':True})
    assert one.status_code == two.status_code == 202
    assert one.json()['id'] == two.json()['id']


def test_approval_releases_pending_outbox_atomically(durable_client):
    f, s = env(durable_client)
    brief = build_brief(f, s)
    with f.begin() as db:
        db.get(Brief, brief['brief_id']).status = 'needs_review'
    queued = durable_client.post(f"/api/v1/briefs/{brief['brief_id']}/deliver", json={'confirmed':True}).json()
    assert queued['status'] == 'blocked_review'
    response = durable_client.post(f"/api/v1/briefs/{brief['brief_id']}/approve", json={'reviewed':True})
    assert response.status_code == 200
    with f() as db:
        assert db.get(Delivery, queued['id']).status == 'pending'


def test_cancel_endpoint(durable_client):
    rid = durable_client.post('/api/v1/runs', json={'kind':'brief'}).json()['run_id']
    assert durable_client.post(f'/api/v1/runs/{rid}/cancel', json={'confirmed':True}).status_code == 200
    assert durable_client.post(f'/api/v1/runs/{rid}/cancel', json={'confirmed':True}).status_code == 409
    assert durable_client.post('/api/v1/runs/missing/cancel', json={'confirmed':True}).status_code == 404


def test_unknown_resolution_requires_explicit_confirmation(durable_client):
    f, s = env(durable_client)
    brief = build_brief(f, s)
    ident = durable_client.post(f"/api/v1/briefs/{brief['brief_id']}/deliver", json={'confirmed':True}).json()['id']
    with f.begin() as db:
        db.get(Delivery, ident).status = 'unknown'
    path = f'/api/v1/deliveries/{ident}/resolve'
    assert durable_client.post(path, json={'resolution':'confirmed_not_sent'}).status_code == 422
    assert durable_client.post(path, json={'confirmed':True, 'resolution':'confirmed_sent'}).status_code == 200


def test_preview_does_not_read_arbitrary_path(durable_client):
    f, s = env(durable_client)
    brief = build_brief(f, s)
    ident = durable_client.post(f"/api/v1/briefs/{brief['brief_id']}/deliver", json={'confirmed':True}).json()['id']
    with f.begin() as db:
        row = db.get(Delivery, ident)
        row.status, row.artifact_name = 'file_written', '../../secret.txt'
    assert durable_client.get(f'/api/v1/deliveries/{ident}/preview').status_code == 409


def test_all_new_endpoints_remain_authenticated(durable_client):
    durable_client.app.state.settings.admin_token = 'x' * 32
    assert durable_client.get('/api/v1/automation').status_code == 401
    assert durable_client.get('/api/v1/deliveries').status_code == 401
    assert durable_client.post('/api/v1/automation/run', json={}).status_code == 401
    assert durable_client.get('/api/v1/automation', headers={'Authorization':'Bearer ' + 'x'*32}).status_code == 200
