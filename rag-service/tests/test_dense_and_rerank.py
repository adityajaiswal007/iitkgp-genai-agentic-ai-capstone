"""Dense fusion and reranking, with fake HTTP servers (no network or models needed)."""
import hashlib

import httpx
import numpy as np
import pytest

from regassist_rag import remote
from regassist_rag.config import Settings
from regassist_rag.index import Retriever, build_index
from regassist_rag.lexical import tokenize

from conftest import DATA_DIR

FILES = {"24_CB_CreditDebitCards_2025_upd2026-10-01.pdf", "Circular_3_2018.pdf"}


def _vector(text: str, dim: int = 64) -> list[float]:
    v = np.zeros(dim)
    for token in tokenize(text):
        v[int(hashlib.md5(token.encode()).hexdigest(), 16) % dim] += 1
    return v.tolist()


def _fake_server(request: httpx.Request) -> httpx.Response:
    import json
    body = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        assert body["model"] == "fake-embed"
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": _vector(t)} for i, t in enumerate(body["input"])]})
    if request.url.path.endswith("/rerank"):
        # score = word overlap, reversed order in the payload to prove we map by index
        q = set(tokenize(body["query"]))
        results = [{"index": i, "relevance_score": len(q & set(tokenize(d)))} for i, d in enumerate(body["documents"])]
        return httpx.Response(200, json={"results": results[::-1]})
    return httpx.Response(404)


@pytest.fixture()
def fake_http(monkeypatch):
    calls = []

    def client(ca_cert, timeout):
        def handler(request):
            calls.append(request.url.path)
            return _fake_server(request)
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(remote, "_client", client)
    return calls


def _settings(tmp_path, **extra):
    return Settings(data_dir=DATA_DIR, index_dir=tmp_path, auto_build=False, ocr=False,
                    embedding_base_url="https://embed.local/v1", embedding_model="fake-embed", **extra)


def test_dense_embeddings_are_built_cached_and_fused(tmp_path, fake_http):
    settings = _settings(tmp_path)
    build_index(settings, only_filenames=FILES)
    assert any(p.name.startswith("embeddings-") for p in tmp_path.iterdir())
    embed_calls = fake_http.count("/v1/embeddings")
    build_index(settings, only_filenames=FILES)  # unchanged snapshot: no re-embedding
    assert fake_http.count("/v1/embeddings") == embed_calls

    r = Retriever(settings)
    assert r.embeddings.shape[0] == len(r.chunks)
    hits = r.search_documents("credit card closure seven working days", "RBI")["hits"]
    assert hits[0]["document_id"].startswith("rbi-024")


def test_reranker_reorders_and_failures_fall_back(tmp_path, fake_http, monkeypatch):
    settings = _settings(tmp_path, rerank_base_url="https://rerank.local/v1")
    build_index(settings, only_filenames=FILES)
    r = Retriever(settings)
    ranked = r.rank_chunks("closure of credit card within seven working days", authority="RBI", top_k=5)
    assert "/v1/rerank" in fake_http
    assert "seven working days" in r.chunks[ranked[0].index].text

    monkeypatch.setattr(r.reranker, "rerank", lambda q, d: None)  # server down
    assert r.rank_chunks("credit card closure", authority="RBI", top_k=3)  # still answers
