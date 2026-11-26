"""Behaviour against a shared embedding server with limits like the course's BGE server:
at most 16 texts per request, at most 512 tokens per text, and occasional capacity errors."""
import json

import httpx
import numpy as np
import pytest

from regassist_rag import index as index_module
from regassist_rag import remote
from regassist_rag.config import Settings
from regassist_rag.index import Retriever, build_index
from regassist_rag.remote import EmbeddingClient, estimate_tokens, fit_tokens

from conftest import DATA_DIR

FILES = {"24_CB_CreditDebitCards_2025_upd2026-10-01.pdf", "Circular_17_2019.pdf"}
DIM = 384


class FakeServer:
    """Imitates the shared BGE server. Settings can be changed between calls."""

    def __init__(self):
        self.requests = []
        self.busy_responses = 0      # next N requests get HTTP 503
        self.down = False            # every request fails to connect
        self.max_chars = 2600        # longer inputs are rejected as over the token limit
        self.fail_after = None       # stop answering after this many successful requests

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down or (self.fail_after is not None and len(self.requests) >= self.fail_after):
            raise httpx.ConnectError("connection refused")
        body = json.loads(request.content)
        if self.busy_responses:
            self.busy_responses -= 1
            return httpx.Response(503, json={"error": "server at capacity"})
        texts = body["input"]
        if len(texts) > 16:
            return httpx.Response(400, json={"error": "at most 16 inputs per request"})
        if any(len(t) > self.max_chars for t in texts):
            return httpx.Response(400, json={"error": "input exceeds 512 tokens"})
        self.requests.append(len(texts))
        data = []
        for i, text in enumerate(texts):
            rng = np.random.default_rng(abs(hash(text.split("\n")[-1][:80])) % 2**32)
            data.append({"index": i, "embedding": rng.normal(size=DIM).tolist()})
        return httpx.Response(200, json={"data": data, "model": body["model"]})


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(remote, "_client",
                        lambda ca_cert, timeout: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: None)
    return fake


def settings_for(tmp_path, **extra):
    return Settings(data_dir=DATA_DIR, index_dir=tmp_path, auto_build=False, ocr=False,
                    embedding_base_url="https://103.42.50.41:1035/v1", embedding_model="bge-small-en-v1.5",
                    embedding_query_prefix="Represent this sentence for searching relevant passages: ", **extra)


def test_indexing_respects_batch_size_and_survives_capacity_errors(tmp_path, server):
    server.busy_responses = 3
    build_index(settings_for(tmp_path), only_filenames=FILES)
    assert max(server.requests) <= 16
    r = Retriever(settings_for(tmp_path))
    assert r.dense_status == "ready" and r.embeddings.shape == (len(r.chunks), DIM)


def test_texts_are_fitted_to_the_token_limit_and_long_ones_are_shortened():
    long_text = "regulation " * 900
    fitted = fit_tokens(long_text, 500)
    assert estimate_tokens(fitted) <= 500 and len(fitted) < len(long_text)
    assert estimate_tokens("50,00,000 ₹ भारत") >= 9  # digits, punctuation and Devanagari count conservatively

    fake = FakeServer()
    fake.max_chars = 1000
    client = EmbeddingClient("https://x/v1", "bge-small-en-v1.5", sleep=lambda s: None)
    client.http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    vectors = client.embed(["short text", "word " * 400])  # second passes the estimate but the server refuses it
    assert vectors.shape == (2, DIM)


def test_interrupted_indexing_resumes_instead_of_starting_over(tmp_path, server):
    settings = settings_for(tmp_path)
    server.fail_after = 3                        # server disappears after 3 batches
    build_index(settings, only_filenames=FILES)  # logs an error, keeps keyword search
    assert Retriever(settings).dense_status == "missing"
    partial = next(tmp_path.glob("embeddings-*.partial.f32"))
    saved = partial.stat().st_size // (4 * DIM)
    assert 0 < saved
    server.fail_after = None
    before = len(server.requests)
    build_index(settings, only_filenames=FILES)  # next start resumes
    r = Retriever(settings)
    total = len(r.chunks)
    assert r.dense_status == "ready" and r.embeddings.shape[0] == total
    resumed_texts = sum(server.requests[before:])
    assert resumed_texts == total - saved
    assert not list(tmp_path.glob("embeddings-*.partial.*"))


def test_questions_fall_back_to_keyword_search_when_the_server_is_down(tmp_path, server):
    settings = settings_for(tmp_path)
    build_index(settings, only_filenames=FILES)
    r = Retriever(settings)
    server.down = True
    hits = r.search_documents("credit card closure seven working days", "RBI")["hits"]
    assert hits and hits[0]["document_id"].startswith("rbi-024")
    assert r.dense_status == "degraded" and r.dense_failures == 1
    server.down = False
    r.search_documents("credit card closure seven working days again", "RBI")
    assert r.dense_status == "ready"


def test_each_query_is_embedded_once_for_search_and_fetch(tmp_path, server):
    settings = settings_for(tmp_path)
    build_index(settings, only_filenames=FILES)
    r = Retriever(settings)
    before = len(server.requests)
    query = "monetary limits for departmental appeals"
    hits = r.search_documents(query, "TAX")["hits"]
    r.fetch_passages([h["document_id"] for h in hits], query)
    assert len(server.requests) - before == 1


def test_queries_get_the_bge_instruction_and_documents_do_not(tmp_path, monkeypatch, server):
    seen = []
    original = server.handler

    def recording(request):
        seen.extend(json.loads(request.content)["input"])
        return original(request)

    monkeypatch.setattr(remote, "_client",
                        lambda ca_cert, timeout: httpx.Client(transport=httpx.MockTransport(recording)))
    settings = settings_for(tmp_path)
    build_index(settings, only_filenames={"Circular_17_2019.pdf"})
    documents = list(seen)
    Retriever(settings).search_documents("appeal limits", "TAX")
    assert not any(t.startswith("Represent this sentence") for t in documents)
    assert seen[-1] == "Represent this sentence for searching relevant passages: appeal limits"
