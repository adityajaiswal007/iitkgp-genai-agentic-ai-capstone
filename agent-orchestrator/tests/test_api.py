from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from regulatory_agent.api import create_app
from regulatory_agent.config import Settings
from regulatory_agent.models import ScriptedModel


def settings(tmp_path, **kwargs):
    return Settings(database_path=tmp_path / 'state.sqlite', **kwargs)


def request(question='What is the RBI demo complaint process?', **kwargs):
    return {'conversation_id':'customer_001','question':question,'as_of':'2026-01-01', **kwargs}


def test_health_and_openapi(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get('/health').json() == {'status':'ok','mode':'demo','corpus':'synthetic'}
        spec = client.get('/openapi.json').json()
        assert '/v1/executions' in spec['paths']
        assert client.get('/docs').status_code == 200


def test_execution_releases_citations_not_internal_state(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post('/v1/executions',json=request())
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'answered'
        assert data['conversation_id'] == 'customer_001'
        assert data['answer']['claims'][0]['citations']
        assert data['metrics']['steps'] == 6
        assert data['mode'] == 'demo' and data['corpus'] == 'synthetic'
        assert 'fictional demo' in data['answer_text']
        assert data['trace'] is None
        assert not {'draft','history','evidence','plan'} & data.keys()


def test_followup_after_application_restart(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        assert client.post('/v1/executions',json=request()).json()['status'] == 'answered'
    with TestClient(create_app(config)) as client:
        data = client.post('/v1/executions',json=request('What is the complaint reference follow-up?')).json()
        assert data['status'] == 'answered'
        assert data['answer']['sources'][0]['authority'] == 'RBI'
        assert data['metrics']['steps'] == 6


def test_generated_conversation_and_isolation(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post('/v1/executions',json=request())
        response = client.post('/v1/executions',json={'question':'What about the follow-up?'})
        assert response.status_code == 200
        assert response.json()['conversation_id'] != 'customer_001'
        assert response.json()['status'] == 'clarify'


@pytest.mark.parametrize('payload', [
    request(question='   '), request(question='x'*4001),
    request(conversation_id='../../escape'), request(as_of='2026-02-30'),
    request(fiscal_year='2026'), request(extra='not allowed'),
    request(include_trace='yes'), request(fiscal_year='2025-99'),
])
def test_invalid_requests_are_sanitized(tmp_path,payload):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post('/v1/executions',json=payload)
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'
        assert 'input' not in response.text
        assert 'x'*100 not in response.text


def test_malformed_json(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post('/v1/executions',content='{bad',headers={'Content-Type':'application/json'})
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'


def test_optional_key_protects_execution(tmp_path):
    with TestClient(create_app(settings(tmp_path, api_key='test-service-key'))) as client:
        assert client.get('/health').status_code == 200
        assert client.post('/v1/executions',json=request()).status_code == 401
        assert client.post('/v1/executions',json=request(),headers={'X-API-Key':'wrong'}).status_code == 401
        assert client.post('/v1/executions',json=request(),headers={'X-API-Key':'test-service-key'}).status_code == 200


def test_busy_returns_409_instead_of_waiting(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        app.state.execution_lock.acquire()
        try:
            response = client.post('/v1/executions',json=request())
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'SERVICE_BUSY'
        finally:
            app.state.execution_lock.release()


def test_controlled_failure_returns_no_rejected_claims(tmp_path):
    with TestClient(create_app(settings(tmp_path, max_steps=2))) as client:
        data = client.post('/v1/executions',json=request(include_trace=True)).json()
        assert data['status'] == 'abstained'
        assert data['answer']['claims'] == []
        assert data['metrics']['steps'] == 2
        assert any(e.get('code') == 'STEP_CAP' for e in data['trace'])


def test_unexpected_failure_is_sanitized_and_lock_is_released(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch.object(app.state.agent,'ask',side_effect=RuntimeError('sensitive-internal-detail')):
            response = client.post('/v1/executions',json=request())
            assert response.status_code == 500
            assert 'sensitive-internal-detail' not in response.text
        assert client.post('/v1/executions',json=request()).status_code == 200


def test_live_mode_does_not_silently_use_demo(tmp_path,monkeypatch):
    for name in ('QWEN_BASE_URL','QWEN_MODEL','QWEN_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    with pytest.raises(ValueError,match='QWEN'):
        with TestClient(create_app(settings(tmp_path, mode='qwen'))):
            pass


def test_settings_validate_limits(tmp_path):
    with pytest.raises(ValueError):
        settings(tmp_path,max_steps=11)
    with pytest.raises(ValueError):
        settings(tmp_path,mode='typo')


def test_large_body_rejected(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post('/v1/executions',content='x'*70000,headers={'Content-Type':'application/json'})
        assert response.status_code == 413
        assert response.json()['error']['code'] == 'REQUEST_TOO_LARGE'


def test_qwen_adapter_executes_against_local_compatible_server(tmp_path,monkeypatch):
    """Exercise real HTTP serialization/SDK without spending a provider quota."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from regulatory_agent.contracts import Plan, Draft, Critique
    from regulatory_agent.models import DemoModel, ROLE_PROMPTS

    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            role = next(name for name,text in ROLE_PROMPTS.items() if text in body['messages'][0]['content'])
            payload = json.loads(body['messages'][1]['content'])
            schema = {'planner':Plan,'executor':Draft,'critic':Critique}[role]
            content,_ = DemoModel().complete(role,payload,schema,900,10)
            response = json.dumps({
                'id':'mock-completion','object':'chat.completion','created':0,'model':'test-qwen3-8b',
                'choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':60,'completion_tokens':40,'total_tokens':100},
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        def log_message(self,*args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    monkeypatch.setenv('QWEN_BASE_URL',f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('QWEN_MODEL','test-qwen3-8b')
    monkeypatch.setenv('QWEN_API_KEY','mock-key-not-a-real-credential')
    monkeypatch.setenv('QWEN_JSON_MODE','json_object')
    monkeypatch.setenv('QWEN_DISABLE_THINKING','1')
    try:
        with TestClient(create_app(settings(tmp_path,mode='qwen'))) as client:
            response = client.post('/v1/executions',json=request())
            assert response.status_code == 200
            assert response.json()['mode'] == 'qwen'
            assert response.json()['status'] == 'answered'
            assert response.json()['metrics']['tokens_charged'] == 300
        assert len(received)==3
        assert all(r['response_format']=={'type':'json_object'} for r in received)
        assert all(r['chat_template_kwargs']=={'enable_thinking':False} for r in received)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
