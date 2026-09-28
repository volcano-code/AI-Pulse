from datetime import datetime, timezone
import pytest
from app.textutil import canonical_url, clean_html, topic_for
from app.timeutil import parse_time, iso
from app.feeds import parse_feed
from app.llm import verify_quote, EvidenceError
from app.schemas import Preferences
from app.config import Settings

@pytest.mark.parametrize("value,expected", [
 ("https://EXAMPLE.com/news?utm_source=x&id=4#here", "https://example.com/news?id=4"),
 ("https://example.com:443/news", "https://example.com/news"),
 ("http://example.com:80", "http://example.com/"),
 ("https://example.com/x?model=v2&fbclid=z", "https://example.com/x?model=v2"),
 ("https://example.com/x?b=2&a=1", "https://example.com/x?b=2&a=1"),
])
def test_canonical_urls(value, expected):
    assert canonical_url(value) == expected

@pytest.mark.parametrize("value", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "https://u:p@example.com", "not a url", "https://example.com:abc", "https://example.com/\x00x"])
def test_bad_urls(value):
    with pytest.raises(ValueError):
        canonical_url(value)

@pytest.mark.parametrize("value,expected", [
 ("<p>Hello <b>world</b></p>", "Hello world"),
 ("A<script>alert(1)</script>B", "A B"),
 ("<style>bad</style>Visible", "Visible"),
 ("&lt;b&gt;literal&lt;/b&gt;", "<b>literal</b>"),
])
def test_html(value, expected):
    assert clean_html(value) == expected

@pytest.mark.parametrize("value", [None, "", "nonsense", "2026-09-22T12:00:00", "2026-09-22"])
def test_missing_or_uncertain_time(value):
    assert parse_time(value) is None

def test_rfc_time():
    assert iso(parse_time("Tue, 22 Sep 2026 08:00:00 +0800")) == "2026-09-22T00:00:00.000000Z"

def test_iso_time():
    assert iso(parse_time("2026-09-22T08:00:00+08:00")) == "2026-09-22T00:00:00.000000Z"

def test_rss():
    raw = b'<rss><channel><item><title>News</title><link>https://example.com/a</link><description>&lt;p&gt;Hello&lt;/p&gt;</description><pubDate>Tue, 22 Sep 2026 08:00:00 +0800</pubDate></item></channel></rss>'
    item = parse_feed(raw, "rss")[0]
    assert item.text == "Hello"
    assert item.published_at == "2026-09-22T00:00:00.000000Z"

def test_atom_updated_is_not_published():
    raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link href="https://example.com/a"/><summary>Test</summary><updated>2026-09-22T00:00:00Z</updated></entry></feed>'
    item = parse_feed(raw, "atom")[0]
    assert item.published_at is None
    assert item.updated_at is not None

def test_github_filters_drafts():
    raw = b'[{"name":"v1","html_url":"https://github.com/x/y/releases/tag/v1","body":"Release", "published_at":"2026-09-22T00:00:00Z"},{"name":"draft","draft":true}]'
    assert len(parse_feed(raw, "github")) == 1

def test_atom_alternate_not_pdf_link():
    raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link rel="related" href="https://example.com/a.pdf"/><link rel="alternate" href="https://example.com/a"/><summary>body</summary></entry></feed>'
    assert parse_feed(raw, "atom")[0].url == "https://example.com/a"

def test_xml_entity_expansion_blocked():
    raw = b'<!DOCTYPE rss [<!ENTITY x "bomb">]><rss><channel><item><title>&x;</title></item></channel></rss>'
    with pytest.raises(Exception):
        parse_feed(raw, "rss")

def test_feed_limit():
    entry = '<item><title>A</title><link>https://example.com/a</link></item>'
    assert len(parse_feed(('<rss><channel>' + entry * 40 + '</channel></rss>').encode(), 'rss')) == 30

def test_evidence_unicode_offsets():
    text = "前缀 Agent 工作流支持原文快照，后缀"
    quote = "Agent 工作流支持原文快照"
    start, end = verify_quote(quote, text)
    assert text[start:end] == quote

@pytest.mark.parametrize("quote", ["", " ", "不存在的引句"])
def test_evidence_rejects_invalid(quote):
    with pytest.raises(EvidenceError):
        verify_quote(quote, "真实原文")

@pytest.mark.parametrize("changes", [{"timezone":"Bad/Zone"},{"max_items":13},{"max_items":0},{"lookback_hours":169},{"topics":["unsupported"]},{"extra_field":1},{"blocked_keywords":["x"*61]}])
def test_preferences_validation(changes):
    with pytest.raises(ValueError):
        Preferences(**changes)

def test_live_llm_requires_credentials():
    with pytest.raises(ValueError):
        Settings(_env_file=None,llm_mode="live",llm_api_key="",llm_model="")

def test_replay_never_sends_to_paid_model():
    with pytest.raises(ValueError):
        Settings(_env_file=None,data_mode="replay",llm_mode="live",llm_api_key="secret",llm_model="model")

def test_short_admin_token_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None,admin_token="123")

@pytest.mark.parametrize("text,expected",[("Agent 工作流","Agent"),("vision model","多模态"),("language model","LLM")])
def test_topic_rules(text,expected):
    assert topic_for(text)==expected
