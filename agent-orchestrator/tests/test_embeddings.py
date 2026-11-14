"""Hosted query embedding contract; all HTTP here is mocked or local HTTPS."""
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from regulatory_agent.api import create_app
from regulatory_agent.config import Settings
from regulatory_agent.embeddings import EmbeddingClient, EmbeddingError


BASE_URL = 'https://embeddings.example/v1'
MODEL = 'bge-small-en-v1.5'


def response_data(vectors=None):
    vectors = vectors if vectors is not None else [[0.1,-0.2,0.3],[0.4,0.5,-0.6]]
    return {'object':'list','model':MODEL,
            'data':[{'object':'embedding','index':i,'embedding':v} for i,v in enumerate(vectors)],
            'usage':{'prompt_tokens':10,'total_tokens':10}}


def client_for(handler, **kwargs):
    return EmbeddingClient(BASE_URL, client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_payload_order_auth_and_usage():
    seen=[]
    def handler(request):
        seen.append(request)
        data=response_data()
        data['data'].reverse()
        return httpx.Response(200,json=data)
    client=client_for(handler,api_key='embedding-test-key')
    try:
        result=client.embed_queries(['bank complaint','tax filing'])
        assert result.model==MODEL and result.dimensions==3
        assert [entry.index for entry in result.data]==[0,1]
        assert result.data[0].embedding==[0.1,-0.2,0.3]
        assert result.usage.total_tokens==10
        assert seen[0].url==BASE_URL+'/embeddings'
        assert seen[0].headers['Authorization']=='Bearer embedding-test-key'
        assert json.loads(seen[0].content)=={'model':MODEL,'input':['bank complaint','tax filing']}
    finally:
        client.client.close()


def test_no_auth_header_without_embedding_key(monkeypatch):
    monkeypatch.setenv('QWEN_API_KEY','never-use-this-in-embeddings')
    seen=[]
    client=client_for(lambda r:(seen.append(r) or httpx.Response(200,json=response_data([[1.0,2.0]]))))
    try:
        client.embed_queries(['a question'])
        assert 'Authorization' not in seen[0].headers
    finally:
        client.client.close()


@pytest.mark.parametrize('bad', [
    {'data':[],'model':MODEL},
    response_data([[0.1],[0.2,0.3]]),  # Reject truncated/inconsistent dimensions.
    {**response_data(),'model':'different-vector-space'},
    {**response_data(),'data':[response_data()['data'][0]]*2},
    {**response_data(),'data':[dict(d,index=7) for d in response_data()['data']]},
    response_data([[float('nan'),0.1],[0.2,0.3]]),
    response_data([[True,0.1],[0.2,0.3]]),
    response_data([[0.0,0.0],[0.2,0.3]]),
    {**response_data(),'usage':{'prompt_tokens':-1,'total_tokens':10}},
    {**response_data(),'usage':{'prompt_tokens':11,'total_tokens':10}},
])
def test_malformed_provider_vectors_are_rejected(bad):
    client=client_for(lambda r:httpx.Response(200,content=json.dumps(bad).encode()))
    try:
        with pytest.raises(EmbeddingError) as exc:
            client.embed_queries(['question one','question two'])
        assert exc.value.code=='INVALID_EMBEDDING_RESPONSE'
        assert exc.value.status_code==502
    finally:
        client.client.close()


def test_dimension_contract_and_missing_usage():
    client=client_for(lambda r:httpx.Response(200,json={k:v for k,v in response_data([[1.0,2.0]]).items() if k!='usage'}),dimensions=3)
    try:
        with pytest.raises(EmbeddingError):
            client.embed_queries(['question'])
        client.dimensions=2
        assert client.embed_queries(['question']).usage is None
    finally:
        client.client.close()


@pytest.mark.parametrize('status,expected',[(401,502),(403,502),(429,503),(500,503),(302,502)])
def test_errors_are_sanitized_and_not_retried(status,expected):
    seen=[]
    client=client_for(lambda r:(seen.append(r) or httpx.Response(status,text='secret-upstream-body')))
    try:
        with pytest.raises(EmbeddingError) as exc:
            client.embed_queries(['question'])
        assert exc.value.status_code==expected
        assert 'secret-upstream-body' not in str(exc.value)
        assert len(seen)==1
    finally:
        client.client.close()


def test_timeout_maps_to_504():
    def handler(request):
        raise httpx.ReadTimeout('private-details',request=request)
    client=client_for(handler)
    try:
        with pytest.raises(EmbeddingError) as exc:
            client.embed_queries(['question'])
        assert exc.value.status_code==504
        assert 'private-details' not in str(exc.value)
    finally:
        client.client.close()


@pytest.mark.parametrize('queries',[[],[''],['   '],['x'*2001],['question']*9,['question',2],['😀'*2000]*3,'not-a-list'])
def test_input_is_bounded_before_network(queries):
    seen=[]
    client=client_for(lambda r:(seen.append(r) or httpx.Response(500)))
    try:
        with pytest.raises(ValueError):
            client.embed_queries(queries)
        assert seen==[]
    finally:
        client.client.close()


def test_response_body_is_bounded():
    client=client_for(lambda r:httpx.Response(200,content=b'x'*(1024*1024+1)))
    try:
        with pytest.raises(EmbeddingError,match='response'):
            client.embed_queries(['question'])
    finally:
        client.client.close()


def test_empty_config_keeps_existing_execution_working(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path/'state.sqlite'))) as api:
        result=api.post('/v1/query-embeddings',json={'input':['bank complaint']})
        assert result.status_code==503
        assert result.json()['error']['code']=='EMBEDDING_NOT_CONFIGURED'
        assert api.post('/v1/executions',json={'question':'RBI demo complaint'}).status_code==200


def test_api_vectors_auth_lock_and_no_retrieval_change(tmp_path):
    provider=client_for(lambda r:httpx.Response(200,json=response_data([[0.1,0.2]])))
    config=Settings(database_path=tmp_path/'state.sqlite',embedding_base_url=BASE_URL,api_key='service-key')
    try:
        with patch('regulatory_agent.api.EmbeddingClient',return_value=provider):
            app=create_app(config)
            with TestClient(app) as api:
                assert api.post('/v1/query-embeddings',json={'input':['query']}).status_code==401
                headers={'X-API-Key':'service-key'}
                result=api.post('/v1/query-embeddings',json={'input':['query']},headers=headers)
                assert result.status_code==200
                assert result.json()['dimensions']==2
                assert 'query' not in result.text
                app.state.execution_lock.acquire()
                try:
                    assert api.post('/v1/query-embeddings',json={'input':['query']},headers=headers).status_code==409
                finally:
                    app.state.execution_lock.release()
                assert '/v1/query-embeddings' in api.get('/openapi.json').json()['paths']
    finally:
        provider.client.close()


def test_api_malformed_input_and_upstream_error(tmp_path):
    provider=client_for(lambda r:httpx.Response(500,text='secret-body'))
    config=Settings(database_path=tmp_path/'state.sqlite',embedding_base_url=BASE_URL)
    try:
        with patch('regulatory_agent.api.EmbeddingClient',return_value=provider):
            with TestClient(create_app(config)) as api:
                assert api.post('/v1/query-embeddings',json={'input':[' ']}).status_code==422
                assert api.post('/v1/query-embeddings',json={'input':['query'],'model':'another'}).status_code==422
                result=api.post('/v1/query-embeddings',json={'input':['query']})
                assert result.status_code==503 and 'secret-body' not in result.text
    finally:
        provider.client.close()


def test_settings_load_embedding_values_without_exposing_key(monkeypatch):
    monkeypatch.setenv('EMBEDDING_BASE_URL',BASE_URL)
    monkeypatch.setenv('EMBEDDING_MODEL',MODEL)
    monkeypatch.setenv('EMBEDDING_API_KEY','private-embedding-key')
    monkeypatch.setenv('EMBEDDING_CA_CERT','/certificate/embedding.crt')
    monkeypatch.setenv('EMBEDDING_DIMENSIONS','384')
    config=Settings.from_env()
    assert config.embedding_base_url==BASE_URL
    assert config.embedding_dimensions==384
    assert config.embedding_ca_cert=='/certificate/embedding.crt'
    assert 'private-embedding-key' not in repr(config)


def test_configuring_embeddings_does_not_add_paid_calls_to_execution(tmp_path):
    seen=[]
    provider=client_for(lambda r:(seen.append(r) or httpx.Response(500)))
    config=Settings(database_path=tmp_path/'state.sqlite',embedding_base_url=BASE_URL)
    try:
        with patch('regulatory_agent.api.EmbeddingClient',return_value=provider):
            with TestClient(create_app(config)) as api:
                result=api.post('/v1/executions',json={'question':'RBI demo complaint'}).json()
                assert result['status']=='answered' and result['metrics']['steps']==6
        assert seen==[]
    finally:
        provider.client.close()


@pytest.fixture
def embedding_https_server(tmp_path,request):
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import trustme
    ca=trustme.CA()
    certificate=ca.issue_cert(*getattr(request,'param',('127.0.0.1',)))
    ca_path=tmp_path/'embedding.crt'
    ca.cert_pem.write_to_path(ca_path)
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body=json.dumps(response_data([[0.1,0.2]])).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(context)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}/v1',ca_path,received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_actual_https_custom_ca_and_payload(embedding_https_server):
    url,path,received=embedding_https_server
    client=EmbeddingClient(url,ca_cert=path)
    try:
        assert client.embed_queries(['bank complaint']).dimensions==2
        assert received==[{'model':MODEL,'input':['bank complaint']}]
    finally:
        client.close()
    assert client.client.is_closed


def test_actual_https_untrusted_ca_rejected(embedding_https_server):
    url,_,received=embedding_https_server
    client=EmbeddingClient(url)
    try:
        with pytest.raises(EmbeddingError) as exc:
            client.embed_queries(['question'])
        assert exc.value.status_code==503
        assert received==[]
    finally:
        client.close()


@pytest.mark.parametrize('embedding_https_server',[('another-host.example',)],indirect=True)
def test_actual_https_hostname_still_verified(embedding_https_server):
    url,path,received=embedding_https_server
    client=EmbeddingClient(url,ca_cert=path)
    try:
        with pytest.raises(EmbeddingError):
            client.embed_queries(['question'])
        assert received==[]
    finally:
        client.close()


def test_bad_ca_fails_before_any_request(tmp_path):
    with pytest.raises(ValueError,match='EMBEDDING_CA_CERT'):
        EmbeddingClient(BASE_URL,ca_cert=tmp_path/'missing.crt')
    path=tmp_path/'bad.crt'
    path.write_text('invalid PEM data')
    with pytest.raises(ValueError,match='EMBEDDING_CA_CERT'):
        EmbeddingClient(BASE_URL,ca_cert=path)


@pytest.mark.parametrize('url',[
    'http://remote.example/v1','https://user:secret@remote.example/v1',
    'https://remote.example/v1?key=secret','https://remote.example/v1#fragment',
    'https://remote.example/v1/embeddings','file:///certificate/embedding.crt',
])
def test_base_url_configuration_is_restricted(url):
    with pytest.raises(ValueError,match='EMBEDDING_BASE_URL'):
        EmbeddingClient(url)


def test_api_to_real_local_https_provider_and_cleanup(tmp_path,embedding_https_server):
    url,path,received=embedding_https_server
    config=Settings(database_path=tmp_path/'state.sqlite',embedding_base_url=url,
                    embedding_ca_cert=str(path),embedding_dimensions=2)
    app=create_app(config)
    with TestClient(app) as api:
        result=api.post('/v1/query-embeddings',json={'input':['bank complaint']})
        assert result.status_code==200
        assert result.json()['data'][0]['embedding']==[0.1,0.2]
        assert result.json()['usage']['total_tokens']==10
    assert received==[{'model':MODEL,'input':['bank complaint']}]
    assert app.state.embeddings.client.is_closed


@pytest.mark.parametrize('body',[b'{broken',b'not json',b''])
def test_malformed_provider_json(body):
    client=client_for(lambda r:httpx.Response(200,content=body))
    try:
        with pytest.raises(EmbeddingError) as exc:
            client.embed_queries(['question'])
        assert exc.value.code=='INVALID_EMBEDDING_RESPONSE'
    finally:
        client.client.close()


def test_slow_stream_has_elapsed_time_check():
    import time
    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            time.sleep(0.03)
            yield b'{'
    client=client_for(lambda r:httpx.Response(200,stream=Slow()),timeout=0.01)
    try:
        with pytest.raises(EmbeddingError) as exc:
            client.embed_queries(['question'])
        assert exc.value.code=='EMBEDDING_TIMEOUT'
    finally:
        client.client.close()
