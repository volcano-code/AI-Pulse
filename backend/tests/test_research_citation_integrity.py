"""Quote proof must use nonempty, exact, original Unicode string offsets."""
import pytest
from sqlalchemy import select
from app.models import Article, Snapshot
from app.research import execute_research
from app.schemas import ResearchRequest
from app.textutil import digest


def evidence(client, text):
    factory = client.app.state.session_factory
    with factory.begin() as db:
        article = Article(source_id='huggingface', canonical_url='https://example.invalid/citation',
                          title='agent evidence', data_mode='replay')
        db.add(article)
        db.flush()
        snapshot = Snapshot(article_id=article.id, title=article.title,
                            text=text, content_hash=digest(text))
        db.add(snapshot)
        db.flush()
        article.current_snapshot_id = snapshot.id
        return snapshot.id


def investigate(client, budget=3):
    return execute_research(client.app.state.session_factory, client.app.state.settings,
                            ResearchRequest(question='agent', max_tool_calls=budget))


@pytest.mark.parametrize('prefix', ['İ', 'ß'])
def test_casefold_expansion_keeps_original_nonempty_offsets(client, prefix):
    text = prefix * 100 + 'agent'
    evidence(client, text)
    row = investigate(client)
    assert row['status'] == 'completed'
    assert row['result']['citations']
    for citation in row['result']['citations']:
        start, end = citation['quote_start'], citation['quote_end']
        assert type(start) is int and type(end) is int
        assert 0 <= start < end <= len(text)
        assert citation['quote'] == text[start:end]
        assert 'agent' in citation['quote']


@pytest.mark.parametrize('start,end,quote', [
    (-2, 5, 'DE'), (False, True, 'A'), (0, 999, 'ABCDE'),
    (4, 2, ''), (2, 2, ''), (0, 0, ''),
])
@pytest.mark.parametrize('budget', [1, 3])
def test_invalid_quote_offsets_fail_closed(client, monkeypatch, start, end, quote, budget):
    snapshot_id = evidence(client, 'ABCDE')
    monkeypatch.setattr('app.research.search_research_evidence', lambda *args, **kwargs: {
        'citations': [{'snapshot_id': snapshot_id, 'quote_start': start, 'quote_end': end, 'quote': quote}],
        'searched_documents': 1, 'requested_mode': 'lexical', 'effective_mode': 'lexical',
        'degraded': False, 'degraded_reason': None,
    })
    row = investigate(client, budget)
    assert row['status'] == 'needs_attention'
    assert row['result']['citations'] == []
    assert row['result']['claims'] == []
    assert row['result']['abstained'] is True


def test_valid_chinese_non_bmp_quote_survives_both_gates(client, monkeypatch):
    text = '前言：中文😀证据𠮷内容。结尾'
    quote = '中文😀证据𠮷内容'
    start = text.index(quote)
    end = start + len(quote)
    snapshot_id = evidence(client, text)
    monkeypatch.setattr('app.research.search_research_evidence', lambda *args, **kwargs: {
        'citations': [{'snapshot_id': snapshot_id, 'quote_start': start, 'quote_end': end, 'quote': quote}],
        'searched_documents': 1, 'requested_mode': 'lexical', 'effective_mode': 'lexical',
        'degraded': False, 'degraded_reason': None,
    })
    row = investigate(client)
    assert row['status'] == 'completed'
    citation, = row['result']['citations']
    assert citation['quote'] == text[citation['quote_start']:citation['quote_end']] == quote
    assert any(event.get('tool') == 'inspect_evidence' for event in row['trace'])


def test_generated_claims_cannot_bypass_final_quote_gate(client, monkeypatch):
    from test_reading_research import FakeModel, tool_call, final
    snapshot_id = evidence(client, 'ABCDE')
    monkeypatch.setattr('app.research.search_research_evidence', lambda *args, **kwargs: {
        'citations': [{'snapshot_id': snapshot_id, 'quote_start': 0, 'quote_end': 999, 'quote': 'ABCDE'}],
        'searched_documents': 1, 'requested_mode': 'lexical', 'effective_mode': 'lexical',
        'degraded': False, 'degraded_reason': None,
    })
    settings = client.app.state.settings.model_copy(update={'llm_mode': 'live'})
    model = FakeModel([tool_call(), final()])
    row = execute_research(client.app.state.session_factory, settings,
                           ResearchRequest(question='Agent', max_tool_calls=1), model)
    assert model.calls == 2  # scripted local double, never a provider request
    assert row['status'] == 'needs_attention'
    assert row['result']['stop_reason'] == 'evidence_changed'
    assert row['result']['claims'] == [] and row['result']['citations'] == []
