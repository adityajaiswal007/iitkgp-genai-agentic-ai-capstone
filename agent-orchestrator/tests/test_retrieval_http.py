import httpx
import pytest
from regulatory_agent.contracts import SearchArgs, FetchArgs
from regulatory_agent.tools import HttpCorpusTools


def test_http_adapter_preserves_tools_and_query():
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path.endswith('search_documents'):
            return httpx.Response(200, json={'hits':[{'document_id':'OP-204','score':0.8}]})
        return httpx.Response(200, json={'passages':[]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = HttpCorpusTools('http://localhost:8001', api_key='test', client=client)
        assert tools.search_documents(SearchArgs(query='Valve-B', authority='AVL'))['hits'][0]['document_id'] == 'OP-204'
        tools.fetch_passages(FetchArgs(document_ids=['OP-204'], query='Valve-B'))
    assert seen[0].headers['X-API-Key'] == 'test'
    assert b'Valve-B' in seen[1].content


@pytest.mark.parametrize('status,exception', [(404,KeyError),(429,ConnectionError),(503,ConnectionError),(401,RuntimeError)])
def test_http_error_mapping(status, exception):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        tools = HttpCorpusTools('http://localhost:8001', client=client)
        with pytest.raises(exception):
            tools.fetch_passages(FetchArgs(document_ids=['OP-204']))


@pytest.mark.parametrize('question,document_id,citation,text', [
    ('What is AVL Valve-B pressure?', 'BOM-FLT-H2NEX', 'valve:1', 'Maximum operating pressure: 2,800 psi'),
    ('share the Regional Operational Leadership Assignments', 'RGM-2026-PDF', 'rgm:page1',
     'Regional Operational Leadership Assignments: Americas — Sarah Jenkins; EMEA — Marcus Vance; APAC — Kenji Sato.'),
    ('Who are the regional compliance officers?', 'RGM-2026-PDF', 'rgm:page2',
     'Regional Compliance Officer Roster: Americas — Patricia Gomez; EMEA — Dr. Klaus Richter; APAC — Aisha Al-Rashid.'),
])
def test_orchestrator_uses_remote_catalog_and_passes_fetch_query(tmp_path, question, document_id, citation, text):
    import json
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from regulatory_agent.api import create_app
    from regulatory_agent.config import Settings
    paths = []
    def handler(request):
        name = request.url.path.rsplit('/',1)[-1]
        payload = json.loads(request.content)
        paths.append(name)
        if name == 'search_documents':
            assert payload['authority'] == 'AVL'
            data = {'hits':[{'document_id':document_id,'score':1.0}]}
        elif name == 'fetch_passages':
            assert payload['query'] == question
            data = {'passages':[{'citation_id':citation,'document_id':document_id,'authority':'AVL',
                    'title':'Corpus document','text':text,
                    'page':1,'section':'Test section','source_url':'urn:avl:'+document_id,'corpus_snapshot':'test'}]}
        else:
            assert 'query' not in payload
            data = {'documents':[{'document_id':document_id,'status':'applicable','reason':'Catalog interval.'}]}
        return httpx.Response(200,json=data)
    class TestTools(HttpCorpusTools):
        def __init__(self, *args):
            super().__init__(*args, client=httpx.Client(transport=httpx.MockTransport(handler)))
            self.owns_client = True
    settings = Settings(database_path=tmp_path/'state.sqlite', retrieval_base_url='http://localhost:8001')
    with patch('regulatory_agent.api.HttpCorpusTools',TestTools):
        with TestClient(create_app(settings)) as client:
            data = client.post('/v1/executions',json={'question':question,'as_of':'2026-11-10'}).json()
            assert data['status'] == 'answered' and data['corpus'] == 'retrieval'
            assert data['answer']['sources'][0]['citation_id'] == citation
            assert text in data['answer_text']
            assert client.get('/health').json()['corpus'] == 'retrieval'
    assert paths == ['search_documents','fetch_passages','lookup_document_status']
