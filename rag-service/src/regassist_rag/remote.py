"""HTTP clients for the optional embedding and reranking servers.

Both speak widely used formats:
  * embeddings: OpenAI-compatible  POST {base}/embeddings  {"model", "input": [...]}
  * rerank:     Cohere/Jina style  POST {base}/rerank      {"model", "query", "documents", "top_n"}
    (served by vLLM, Text Embeddings Inference, Infinity, Jina and Cohere-compatible gateways)
Set the base URL including any version prefix, e.g. https://host:port/v1.

The embedding client respects a shared server's limits: a maximum number of texts per
request, a maximum number of tokens per text, and retries when the server is at capacity.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Callable

import httpx
import numpy as np

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
TOO_LONG_STATUS = {400, 413, 422}
_PIECE = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


def estimate_tokens(text: str) -> int:
    """Conservative estimate for BERT-style WordPiece tokenizers (BGE, e5, MiniLM).

    Long English words split into several pieces, digits into groups of about three,
    and each punctuation mark or non-Latin character (such as Devanagari) counts as one.
    The real count is usually lower, so text that fits by this estimate fits the model.
    """
    count = 0
    for piece in _PIECE.findall(text):
        if piece.isascii() and piece.isalpha():
            count += 1 + (len(piece) - 1) // 6
        elif piece.isdigit():
            count += math.ceil(len(piece) / 3)
        else:
            count += 1
    return count


def fit_tokens(text: str, max_tokens: int) -> str:
    """Trim text from the end until its estimated token count fits."""
    if estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:  # longest prefix that fits
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    cut = text.rfind(" ", 0, low)
    return text[: cut if cut > low * 0.8 else low]


def _client(ca_cert: str, timeout: float) -> httpx.Client:
    verify: str | bool = ca_cert if ca_cert else True
    return httpx.Client(timeout=timeout, verify=verify)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", ca_cert: str = "",
                 batch_size: int = 16, timeout: float = 60.0, max_tokens: int = 512,
                 retries: int = 6, sleep: Callable[[float], None] | None = None):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.batch_size = max(1, batch_size)
        # Leave room for the [CLS] and [SEP] tokens the server adds.
        self.max_tokens = max(16, max_tokens - 12)
        self.retries = max(1, retries)
        self.sleep = sleep or time.sleep
        self.http = _client(ca_cert, timeout)
        self.dimension: int | None = None

    def embed(self, texts: list[str], progress: Callable[[int], None] | None = None) -> np.ndarray:
        texts = [fit_tokens(t, self.max_tokens) for t in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[start:start + self.batch_size]))
            if progress:
                progress(len(vectors))
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        status, body = self._post(batch)
        if status == 200:
            return self._vectors(body, len(batch))
        if status in TOO_LONG_STATUS:
            # Usually one text is too long for the model. Find it and shorten it.
            if len(batch) > 1:
                return [vector for text in batch for vector in self._embed_batch([text])]
            text = batch[0]
            for _ in range(4):
                text = text[: max(1, int(len(text) * 0.7))]
                status, body = self._post([text])
                if status == 200:
                    log.warning("Embedding input shortened to %d characters to fit the model", len(text))
                    return self._vectors(body, 1)
                if status not in TOO_LONG_STATUS:
                    break
        raise EmbeddingError(f"Embedding server returned HTTP {status}: {str(body)[:300]}")

    def _post(self, batch: list[str]) -> tuple[int, object]:
        """POST one batch, retrying with increasing waits on capacity errors and network faults."""
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                response = self.http.post(f"{self.base_url}/embeddings", headers=_headers(self.api_key),
                                          json={"model": self.model, "input": batch})
                if response.status_code not in RETRYABLE_STATUS:
                    try:
                        return response.status_code, response.json()
                    except ValueError:
                        return response.status_code, response.text
                reason = f"HTTP {response.status_code}"
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                reason = type(exc).__name__
            if attempt == self.retries:
                raise EmbeddingError(f"Embedding server unavailable after {attempt} attempts ({reason})")
            log.warning("Embedding server busy or unreachable (%s); retrying in %.0fs", reason, delay)
            self.sleep(delay)
            delay = min(delay * 2, 30.0)
        raise AssertionError("unreachable")

    def _vectors(self, body: object, expected: int) -> list[list[float]]:
        try:
            data = sorted(body["data"], key=lambda item: item["index"])  # type: ignore[index]
            vectors = [item["embedding"] for item in data]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"Unexpected embedding response: {str(body)[:200]}") from exc
        if len(vectors) != expected:
            raise EmbeddingError(f"Asked for {expected} embeddings, received {len(vectors)}")
        for vector in vectors:
            if self.dimension is None:
                self.dimension = len(vector)
            elif len(vector) != self.dimension:
                raise EmbeddingError("Embedding server returned vectors of different sizes")
        return vectors

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
