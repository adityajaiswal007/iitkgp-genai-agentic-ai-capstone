"""HTTP clients for the optional embedding and reranking servers.

Both speak widely used formats:
  * embeddings: OpenAI-compatible  POST {base}/embeddings  {"model", "input": [...]}
  * rerank:     Cohere/Jina style  POST {base}/rerank      {"model", "query", "documents", "top_n"}
    (served by vLLM, Text Embeddings Inference, Infinity, Jina and Cohere-compatible gateways)
Set the base URL including any version prefix, e.g. https://host:port/v1.
"""
from __future__ import annotations

import logging

import httpx
import numpy as np

log = logging.getLogger(__name__)


def _client(ca_cert: str, timeout: float) -> httpx.Client:
    verify: str | bool = ca_cert if ca_cert else True
    return httpx.Client(timeout=timeout, verify=verify)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", ca_cert: str = "",
                 batch_size: int = 32, timeout: float = 60.0):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.batch_size = batch_size
        self.http = _client(ca_cert, timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            response = self.http.post(f"{self.base_url}/embeddings", headers=_headers(self.api_key),
                                      json={"model": self.model, "input": batch})
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors.extend(item["embedding"] for item in data)
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def close(self):
        self.http.close()


class RerankClient:
    def __init__(self, base_url: str, model: str = "", api_key: str = "", ca_cert: str = "",
                 timeout: float = 20.0):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.http = _client(ca_cert, timeout)

    def rerank(self, query: str, documents: list[str]) -> list[float] | None:
        """Return one relevance score per document, or None if the server failed."""
        if not documents:
            return []
        payload = {"query": query, "documents": documents, "top_n": len(documents)}
        if self.model:
            payload["model"] = self.model
        try:
            response = self.http.post(f"{self.base_url}/rerank", headers=_headers(self.api_key), json=payload)
            response.raise_for_status()
            body = response.json()
            results = body.get("results", body) if isinstance(body, dict) else body
            scores = [float("-inf")] * len(documents)
            for item in results:
                score = item.get("relevance_score", item.get("score"))
                scores[int(item["index"])] = float(score)
            return scores
        except Exception as exc:  # reranking is an enhancement; fall back to fused order
            log.warning("Rerank failed, using fused ranking: %s", exc)
            return None

    def close(self):
        self.http.close()
