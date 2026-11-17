import pytest
from app.research import execute_research
from app.schemas import ResearchRequest


@pytest.mark.parametrize('question', ['智能体' + '测' * 397, '智能体大模型开源论文' + '测' * 389, '智能体' + '测' * 391])
@pytest.mark.parametrize('budget', [1, 3])
def test_legal_long_question_without_evidence_abstains_cleanly(client, question, budget):
    assert len(question) <= 400
    result = execute_research(client.app.state.session_factory, client.app.state.settings,
                              ResearchRequest(question=question, max_tool_calls=budget))
    assert result['status'] == 'completed'
    assert result['result']['stop_reason'] == 'insufficient_evidence'
    assert result['result']['citations'] == [] and result['result']['claims'] == []
    assert result['result']['model_calls'] == 0
    searches = [x['query'] for x in result['trace'] if x.get('tool') == 'search_saved_evidence']
    assert all(len(q) <= 400 for q in searches)
    assert len(searches) <= budget


def test_bounded_expansion_still_recovers_english_evidence(client):
    from test_research_citation_integrity import evidence
    evidence(client, 'agent evidence with no Chinese terms')
    row = execute_research(client.app.state.session_factory, client.app.state.settings,
                           ResearchRequest(question='智能体'))
    searches = [x['query'] for x in row['trace'] if x.get('tool') == 'search_saved_evidence']
    assert searches == ['智能体', '智能体 agent']
    assert row['status'] == 'completed' and row['result']['citations']
    assert row['result']['model_calls'] == 0
