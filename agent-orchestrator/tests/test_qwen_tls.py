"""Certificate verification tests against a local HTTPS server; no paid inference."""
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import trustme
from openai import APIConnectionError

from regulatory_agent.models import QwenModel


@pytest.fixture(autouse=True)
def isolated_qwen_environment(monkeypatch):
    for name in ('QWEN_CA_CERT','QWEN_JSON_MODE','QWEN_DISABLE_THINKING'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('QWEN_BASE_URL','https://127.0.0.1:1/v1')
    monkeypatch.setenv('QWEN_API_KEY','local-test-key')
    monkeypatch.delenv('QWEN_MODEL',raising=False)


@pytest.fixture
def https_endpoint(tmp_path,request):
    ca = trustme.CA()
    cert = ca.issue_cert(*getattr(request,'param',('localhost','127.0.0.1')))
    ca_path = tmp_path/'ca.crt'
    ca.cert_pem.write_to_path(ca_path)
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path,self.headers.get('Authorization')))
            body=json.dumps({'object':'list','data':[]}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert.configure_cert(context)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}/v1',ca_path,received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_custom_ca_connects_and_sends_bearer_key(https_endpoint,monkeypatch):
    url,ca_path,received=https_endpoint
    monkeypatch.setenv('QWEN_BASE_URL',url)
    monkeypatch.setenv('QWEN_CA_CERT',str(ca_path))
    model=QwenModel()
    try:
        assert model.model=='Qwen/Qwen3-8B'
        assert model.client.models.list().data==[]
        assert received==[('/v1/models','Bearer local-test-key')]
    finally:
        model.client.close()


def test_untrusted_certificate_is_rejected(https_endpoint,monkeypatch):
    url,_,received=https_endpoint
    monkeypatch.setenv('QWEN_BASE_URL',url)
    model=QwenModel()
    try:
        with pytest.raises(APIConnectionError):
            model.client.models.list()
        assert received==[]
    finally:
        model.client.close()


@pytest.mark.parametrize('https_endpoint',[('different-host.example',)],indirect=True)
def test_trusted_ca_does_not_bypass_hostname_verification(https_endpoint,monkeypatch):
    url,path,received=https_endpoint
    monkeypatch.setenv('QWEN_BASE_URL',url)
    monkeypatch.setenv('QWEN_CA_CERT',str(path))
    model=QwenModel()
    try:
        with pytest.raises(APIConnectionError):
            model.client.models.list()
        assert received==[]
    finally:
        model.client.close()


def test_constructor_accepts_connection_values(https_endpoint):
    url,path,received=https_endpoint
    model=QwenModel(base_url=url,api_key='explicit-test-key',ca_cert=str(path))
    try:
        assert model.client.models.list().data==[]
        assert received[0][1]=='Bearer explicit-test-key'
    finally:
        model.client.close()


def test_invalid_ca_file_fails_at_startup(tmp_path,monkeypatch):
    path=tmp_path/'bad.crt'
    path.write_text('not a certificate', encoding='utf-8')
    monkeypatch.setenv('QWEN_CA_CERT',str(path))
    with pytest.raises(ValueError,match='QWEN_CA_CERT'):
        QwenModel()


def test_missing_ca_file_fails_at_startup(tmp_path,monkeypatch):
    monkeypatch.setenv('QWEN_CA_CERT',str(tmp_path/'missing.crt'))
    with pytest.raises(ValueError,match='QWEN_CA_CERT'):
        QwenModel()


def test_ca_path_expands_environment_variables(https_endpoint,monkeypatch):
    url,path,_=https_endpoint
    monkeypatch.setenv('QWEN_BASE_URL',url)
    monkeypatch.setenv('TEST_QWEN_CA_DIR',str(path.parent))
    monkeypatch.setenv('QWEN_CA_CERT','$TEST_QWEN_CA_DIR/ca.crt')
    model=QwenModel()
    try:
        assert model.client.models.list().data==[]
    finally:
        model.client.close()
