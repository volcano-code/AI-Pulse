from app.bootstrap import DEFAULT_SOURCES
from scripts import source_canary as canary


def test_canary_uses_same_endpoints_as_production_ingestion():
    assert canary.SOURCES == tuple((k, url, kind) for k, _, url, kind in DEFAULT_SOURCES)
    arxiv = next(item for item in canary.SOURCES if item[0] == 'arxiv')
    assert arxiv[2] == 'atom' and '/api/query?' in arxiv[1]


def test_http_200_empty_feed_is_not_success(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, n): return b'<rss><channel></channel></rss>'
    class Opener:
        def open(self, request, timeout): return Response()
    monkeypatch.setattr(canary.urllib.request, 'build_opener', lambda *args: Opener())
    result = canary.check_source('fixture', 'https://example.org/feed', 'rss')
    assert result['http_status'] == 200
    assert result['status'] == 'empty' and result['items'] == 0
    assert result['error_code'] == 'empty_feed'
