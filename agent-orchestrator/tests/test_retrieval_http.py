import httpx
import pytest
from regulatory_agent.contracts import SearchArgs, FetchArgs
from regulatory_agent.tools import HttpCorpusTools


def test_http_adapter_preserves_tools_and_query():
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path.endswith('search_documents'):
            return httpx.Response(200, json={'hits':[{'document_id':'RBI-COMPLAINT','score':0.8}]})
        return httpx.Response(200, json={'passages':[]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tools = HttpCorpusTools('http://localhost:8001', api_key='test', client=client)
        assert tools.search_documents(SearchArgs(query='complaint reference', authority='RBI'))['hits'][0]['document_id'] == 'RBI-COMPLAINT'
        tools.fetch_passages(FetchArgs(document_ids=['RBI-COMPLAINT'], query='complaint reference'))
    assert seen[0].headers['X-API-Key'] == 'test'
    assert b'complaint reference' in seen[1].content


@pytest.mark.parametrize('status,exception', [(404,KeyError),(429,ConnectionError),(503,ConnectionError),(401,RuntimeError)])
def test_http_error_mapping(status, exception):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        tools = HttpCorpusTools('http://localhost:8001', client=client)
        with pytest.raises(exception):
            tools.fetch_passages(FetchArgs(document_ids=['RBI-COMPLAINT']))


@pytest.mark.parametrize('authority,question,document_id,citation,text', [
    ('RBI', 'What is the RBI demo complaint procedure?', 'RBI-COMPLAINT', 'complaint:1',
     'In this synthetic example, record the complaint reference.'),
    ('SEBI', 'What is the SEBI demo grievance procedure?', 'SEBI-GRIEVANCE', 'grievance:1',
     'In this synthetic example, keep the grievance reference and consult the intermediary.'),
    ('SEBI', 'What is the SEBI demo disclosure interval?', 'SEBI-DISCLOSURE', 'disclosure:1',
     'In this synthetic example, retain the acknowledgment for 2,800 days.'),
])
def test_orchestrator_uses_remote_catalog_and_passes_fetch_query(tmp_path, authority, question, document_id, citation, text):
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
            assert payload['authority'] == authority
            data = {'hits':[{'document_id':document_id,'score':1.0}]}
        elif name == 'fetch_passages':
            assert payload['query'] == question
            data = {'passages':[{'citation_id':citation,'document_id':document_id,'authority':authority,
                    'title':'Corpus document','text':text,
                    'page':1,'section':'Test section','source_url':'urn:regulatory:'+document_id,'corpus_snapshot':'test'}]}
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



def test_empty_sebi_catalog_reports_missing_documents(tmp_path):
    from regulatory_agent.agent import RegulatoryAgent
    from regulatory_agent.models import DemoModel

    def search(request):
        assert request.url.path == '/v1/tools/search_documents'
        return httpx.Response(200, json={'hits':[]})

    with httpx.Client(transport=httpx.MockTransport(search)) as client:
        adapter = HttpCorpusTools('http://localhost:8001', client=client)
        agent = RegulatoryAgent(DemoModel(), adapter, tmp_path/'missing.sqlite')
        try:
            state = agent.ask('sebi-missing', 'What does SEBI say about trading windows?')
        finally:
            agent.close()
    assert state['status'] == 'abstained'
    assert state['answer']['limitations'] == ['No matching SEBI documents were found in the retrieval corpus.']
