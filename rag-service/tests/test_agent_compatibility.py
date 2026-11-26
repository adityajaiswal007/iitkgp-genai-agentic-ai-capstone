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


def test_agent_adapter_runs_all_three_tools_for_tax(adapter):
    search = tools.tool_attempt(adapter, "search_documents",
                                {"query": "Circular 17/2019 monetary limits departmental appeals",
                                 "authority": "TAX", "limit": 3}, 15)
    assert search.ok, search.error
    ids = [h["document_id"] for h in search.data["hits"]]
    assert ids[0] == "tax-circular-17-2019"

    fetch = tools.tool_attempt(adapter, "fetch_passages", {"document_ids": ids, "query": "monetary limits"}, 15)
    assert fetch.ok, fetch.error
    assert all(p["authority"] == "TAX" for p in fetch.data["passages"])

    status = tools.tool_attempt(adapter, "lookup_document_status", {"document_ids": ids, "as_of": "2026-11-26"}, 15)
    assert status.ok, status.error
    reasons = {d["document_id"]: d["reason"] for d in status.data["documents"]}
    assert "1961" in reasons["tax-circular-17-2019"]  # legacy-regime note reaches the agent
