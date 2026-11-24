import pytest
from fastapi.testclient import TestClient

from regulatory_agent.contracts import FetchArgs, SearchArgs, StatusArgs
from regulatory_agent.mock_retrieval import create_mock_app
from regulatory_agent.tools import HttpCorpusTools


def test_http_adapter_retrieves_synthetic_document():
    with TestClient(create_mock_app(api_key='mock-key')) as client:
        assert client.get('/health').json() == {'status': 'ok', 'corpus': 'synthetic'}
        tools = HttpCorpusTools('http://localhost:8001', api_key='mock-key', client=client)
        hits = tools.search_documents(SearchArgs(query='RBI complaint', authority='RBI'))
        document_id = hits['hits'][0]['document_id']
        passages = tools.fetch_passages(FetchArgs(document_ids=[document_id], query='RBI complaint'))
        assert passages['passages'][0]['citation_id'] == f'{document_id}:p1'
        assert passages['passages'][0]['corpus_snapshot'] == 'synthetic-v1'
        status = tools.lookup_document_status(StatusArgs(document_ids=[document_id], as_of='2026-01-01'))
        assert status['documents'][0]['status'] == 'applicable'


def test_mock_validates_tool_requests_and_handles_unknown_documents():
    with TestClient(create_mock_app(api_key='mock-key')) as client:
        url = '/v1/tools/lookup_document_status'
        payload = {'document_ids': ['unknown'], 'as_of': '2026-01-01'}
        assert client.post(url, json=payload).status_code == 401
        client.headers['X-API-Key'] = 'mock-key'
        assert client.post(url, json={**payload, 'as_of': 'invalid'}).status_code == 422
        assert client.post(url, json=payload).json()['documents'][0]['status'] == 'unknown'

        tools = HttpCorpusTools('http://localhost:8001', api_key='mock-key', client=client)
        assert tools.search_documents(SearchArgs(query='complaint', authority='SEBI')) == {'hits': []}
        with pytest.raises(KeyError):
            tools.fetch_passages(FetchArgs(document_ids=['unknown']))
