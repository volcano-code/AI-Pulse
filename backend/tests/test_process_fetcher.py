import json
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from app.process_fetcher import ProcessFetcher, FetchDeadlineExceeded
from app.fetcher import FetchError


def test_process_fetch_receives_only_fetch_config(settings,monkeypatch):
    from app import process_fetcher
    seen={}
    monkeypatch.setenv('LLM_API_KEY','NEVER-FORWARD')
    monkeypatch.setenv('SMTP_PASSWORD','NEVER-FORWARD')
    def run(cmd,**kwargs):
        seen.update({'cmd':cmd,**kwargs})
        return SimpleNamespace(returncode=0,stdout=b'<rss/>',stderr=b'')
    monkeypatch.setattr(process_fetcher.subprocess,'run',run)
    assert ProcessFetcher(settings).fetch('https://huggingface.co/blog/feed.xml')==b'<rss/>'
    assert 'NEVER-FORWARD' not in str(seen)
    config=json.loads(seen['input'])['settings']
    assert set(config)=={'allowed_fetch_hosts','max_fetch_bytes','fetch_timeout_seconds','fetch_user_agent'}
    assert seen['timeout']==settings.fetch_deadline_seconds


@pytest.mark.parametrize('result',[
    SimpleNamespace(returncode=2,stdout=b'upstream secret',stderr=b'{\"code\":\"connect_error\"}'),
    SimpleNamespace(returncode=0,stdout=b'',stderr=b''),
    SimpleNamespace(returncode=0,stdout=b'x'*1025,stderr=b''),
])
def test_process_rejects_bad_outputs(settings,monkeypatch,result):
    from app import process_fetcher
    monkeypatch.setattr(process_fetcher.subprocess,'run',lambda *a,**k:result)
    with pytest.raises(FetchError):
        ProcessFetcher(settings.model_copy(update={'max_fetch_bytes':1024})).fetch('https://huggingface.co')


def test_hung_child_is_really_killed_and_waited(settings,monkeypatch,tmp_path):
    """Real process fault injection; not a real network source success."""
    from app import process_fetcher
    native_run=subprocess.run
    pidfile=tmp_path/'pid'
    def run(cmd,**kwargs):
        cmd=[sys.executable,'-c',f'import os,time;open({str(pidfile)!r},"w").write(str(os.getpid()));time.sleep(30)']
        return native_run(cmd,**kwargs)
    monkeypatch.setattr(process_fetcher.subprocess,'run',run)
    started=time.monotonic()
    with pytest.raises(FetchDeadlineExceeded):
        ProcessFetcher(settings.model_copy(update={'fetch_deadline_seconds':2})).fetch('https://huggingface.co')
    assert 1.8 < time.monotonic()-started < 6
    if sys.platform!='win32':
        import os
        with pytest.raises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)


def test_child_rejects_disallowed_url_without_network():
    """Invoke the actual module, without external HTTP or mocked parser."""
    result=subprocess.run([sys.executable,'-m','app.fetch_process'],input=json.dumps({'url':'http://127.0.0.1/','settings':{}}).encode(),capture_output=True,timeout=5)
    assert result.returncode==2 and result.stdout==b''


def test_process_surfaces_only_safe_failure_code(settings,monkeypatch):
    from app import process_fetcher
    result=SimpleNamespace(returncode=2,stdout=b'',stderr=b'{"code":"dns_error","secret":"MUST-NOT-LEAK"}')
    monkeypatch.setattr(process_fetcher.subprocess,'run',lambda *a,**k:result)
    with pytest.raises(FetchError) as exc:
        ProcessFetcher(settings).fetch('https://huggingface.co')
    assert exc.value.code == 'dns_error'
    assert 'MUST-NOT-LEAK' not in str(exc.value)


def test_child_reports_policy_code_without_echoing_url():
    result=subprocess.run([sys.executable,'-m','app.fetch_process'],input=json.dumps({'url':'http://127.0.0.1/private-token','settings':{}}).encode(),capture_output=True,timeout=5)
    assert result.returncode==2 and result.stdout==b''
    diagnostic=json.loads(result.stderr)
    assert diagnostic == {'code':'policy_blocked'}
    assert b'private-token' not in result.stderr
