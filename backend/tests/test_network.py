import socket
import httpx
import pytest
from app.fetcher import SafeFetcher, FetchError, public_https
from app.config import Settings
from app.ingestion import fetch_source
from app.models import Source, FetchRun
from sqlalchemy import select

def public_resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

@pytest.mark.parametrize("url", ["http://huggingface.co/blog/feed.xml", "https://localhost/x", "https://127.0.0.1/x", "https://evil.example/x", "https://u:p@huggingface.co/x", "https://huggingface.co:8443/x"])
def test_allowlist_rejects(url):
    with pytest.raises(FetchError):
        public_https(url,{"huggingface.co"},public_resolver)

@pytest.mark.parametrize("address",["127.0.0.1","10.0.0.1","169.254.169.254","192.168.0.1","::1","fd00::1"])
def test_private_dns_rejected(address):
    with pytest.raises(FetchError):
        public_https("https://huggingface.co/x",{"huggingface.co"},lambda *a,**kw:[(2,1,6,"",(address,443))])

def test_fetch_bound():
    s=Settings(_env_file=None,max_fetch_bytes=1024)
    f=SafeFetcher(s,httpx.MockTransport(lambda r:httpx.Response(200,content=b"x"*1025)),public_resolver)
    with pytest.raises(FetchError):
        f.fetch("https://huggingface.co/blog/feed.xml")

def test_redirect_rechecked():
    f=SafeFetcher(Settings(_env_file=None),httpx.MockTransport(lambda r:httpx.Response(302,headers={"location":"https://localhost/private"})),public_resolver)
    with pytest.raises(FetchError):
        f.fetch("https://huggingface.co/blog/feed.xml")

def test_redirect_limit():
    f=SafeFetcher(Settings(_env_file=None),httpx.MockTransport(lambda r:httpx.Response(302,headers={"location":"/loop"})),public_resolver)
    with pytest.raises(FetchError):
        f.fetch("https://huggingface.co/blog/feed.xml")

def test_fetch_success():
    f=SafeFetcher(Settings(_env_file=None),httpx.MockTransport(lambda r:httpx.Response(200,content=b"<rss/>")),public_resolver)
    assert f.fetch("https://huggingface.co/blog/feed.xml")==b"<rss/>"

def test_fetch_failure_recorded(client):
    class Broken:
        def fetch(self, url):
            raise httpx.ConnectError("credentials-that-must-not-leak")
    settings=client.app.state.settings.model_copy(update={"data_mode":"live"})
    result=fetch_source(client.app.state.session_factory,"huggingface",settings,Broken())
    assert result["status"]=="failed" and result['failure_code']=='source_error'
    assert "credentials-that-must-not-leak" not in str(result)
    with client.app.state.session_factory() as db:
        s=db.get(Source,"huggingface")
        assert s.failure_count==1 and s.last_success_at is None
        assert db.scalar(select(FetchRun)).status=="failed"

def test_empty_feed_not_reported_fresh(client):
    class Empty:
        def fetch(self,url): return b'<rss><channel/></rss>'
    settings=client.app.state.settings.model_copy(update={"data_mode":"live"})
    result=fetch_source(client.app.state.session_factory,"huggingface",settings,Empty())
    assert result["status"]=="failed"


def test_parse_failure_has_machine_readable_code(client):
    class Invalid:
        def fetch(self,url): return b'not xml'
    settings=client.app.state.settings.model_copy(update={"data_mode":"live"})
    result=fetch_source(client.app.state.session_factory,"huggingface",settings,Invalid())
    assert result['status']=='failed' and result['failure_code']=='feed_parse_error'
    assert 'not xml' not in str(result)
