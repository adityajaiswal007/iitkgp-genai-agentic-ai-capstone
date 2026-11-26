"""Run the agent-orchestrator's own HTTP adapter and schemas against this service.

Skipped when the agent package is not installed:
    pip install -e ../agent-orchestrator
"""
import pytest

contracts = pytest.importorskip("regulatory_agent.contracts")
tools = pytest.importorskip("regulatory_agent.tools")

from conftest import doc_id  # noqa: E402


@pytest.fixture()
def adapter(client):
    # The agent's adapter, with the in-process test client as its HTTP transport.
    return tools.HttpCorpusTools("http://localhost:8001", client=client)


def test_agent_adapter_runs_all_three_tools_for_rbi(adapter, retriever):
    search = tools.tool_attempt(adapter, "search_documents",
                                {"query": "credit card closure request", "authority": "RBI", "limit": 3}, 15)
    assert search.ok, search.error
    ids = [h["document_id"] for h in search.data["hits"]]
    assert doc_id(retriever, "24_CB_CreditDebitCards_2025_upd2026-10-01.pdf") in ids

    fetch = tools.tool_attempt(adapter, "fetch_passages", {"document_ids": ids, "query": "credit card closure"}, 15)
    assert fetch.ok, fetch.error
    assert all(p["authority"] == "RBI" for p in fetch.data["passages"])

    status = tools.tool_attempt(adapter, "lookup_document_status", {"document_ids": ids, "as_of": "2026-11-26"}, 15)
    assert status.ok, status.error
    assert {d["document_id"] for d in status.data["documents"]} == set(ids)


def test_agent_adapter_maps_missing_documents_to_not_found(adapter):
    result = tools.tool_attempt(adapter, "fetch_passages", {"document_ids": ["rbi-999-nope"]}, 15)
    assert not result.ok and result.error.code == "NOT_FOUND"


def test_rbi_responses_match_agent_schemas_exactly(client, retriever):
    card = doc_id(retriever, "24_CB_CreditDebitCards_2025_upd2026-10-01.pdf")
    contracts.SearchData.model_validate(client.post("/v1/tools/search_documents",
                                                    json={"query": "credit card", "authority": "RBI"}).json())
    contracts.FetchData.model_validate(client.post("/v1/tools/fetch_passages",
                                                   json={"document_ids": [card], "query": "closure"}).json())
    contracts.StatusData.model_validate(client.post("/v1/tools/lookup_document_status",
                                                    json={"document_ids": [card], "as_of": "2026-11-26"}).json())


def test_agent_adapter_rejects_tax_before_http(adapter):
    search = tools.tool_attempt(adapter, 'search_documents', {
        'query':'Circular 17/2019 monetary limits', 'authority':'TAX', 'limit':3,
    }, 15)
    assert not search.ok and search.error.code == 'INVALID_ARGUMENTS'


def test_agent_workflow_with_rag_catalog_cites_rbi_and_reports_missing_sebi(adapter, tmp_path):
    from regulatory_agent.agent import RegulatoryAgent
    from regulatory_agent.models import DemoModel

    agent = RegulatoryAgent(DemoModel(), adapter, tmp_path/'agent.sqlite')
    try:
        rbi = agent.ask('rbi', 'What does RBI say about a bank credit card closure request?',
                        as_of='2026-11-26')
        assert rbi['status'] == 'answered'
        assert all(source['authority'] == 'RBI' for source in rbi['answer']['sources'])
        assert rbi['answer']['sources'][0]['corpus_snapshot'] != 'synthetic-v1'
        assert [event['name'] for event in rbi['trace'] if event['kind']=='tool'] == [
            'search_documents', 'fetch_passages', 'lookup_document_status',
        ]

        sebi = agent.ask('sebi', 'What does SEBI say about trading windows?', as_of='2026-11-26')
        assert sebi['status'] == 'abstained'
        assert sebi['answer']['claims'] == []
        assert 'No matching SEBI documents were found' in sebi['answer']['limitations'][0]

        both = agent.ask('both', 'Compare RBI credit card closure and SEBI trading window requirements.',
                         as_of='2026-11-26')
        assert both['status'] == 'answered'
        assert {source['authority'] for source in both['answer']['sources']} == {'RBI'}
        assert 'No applicable SEBI documents were available for this comparison.' in both['answer']['limitations']
    finally:
        agent.close()
