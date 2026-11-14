"""Bounded hosted query embeddings. No document ingestion, indexing, or search here."""
import math
import os
from pathlib import Path
import ssl
import time
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAX_BATCH = 8
MAX_INPUT_BYTES = 16384
MAX_RESPONSE_BYTES = 1024 * 1024


class QueryEmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    input: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(min_length=1, max_length=MAX_BATCH)

    @field_validator('input')
    @classmethod
    def bounded_nonblank_queries(cls, values):
        if any(not text.strip() for text in values):
            raise ValueError('Queries must not be blank.')
        if sum(len(text.encode('utf-8')) for text in values) > MAX_INPUT_BYTES:
            raise ValueError('Combined query text exceeds 16 KiB.')
        return values


class EmbeddingEntry(BaseModel):
    model_config = ConfigDict(strict=True)
    object: Literal['embedding']
    index: int = Field(ge=0)
    embedding: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(min_length=1, max_length=4096)


class EmbeddingUsage(BaseModel):
    model_config = ConfigDict(strict=True)
    prompt_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode='after')
    def total_includes_input(self):
        if self.total_tokens < self.prompt_tokens:
            raise ValueError('Invalid usage totals.')
        return self


class ProviderEmbeddingResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    object: Literal['list']
    data: list[EmbeddingEntry] = Field(min_length=1, max_length=MAX_BATCH)
    model: str
    usage: EmbeddingUsage | None = None


class QueryEmbeddingResponse(ProviderEmbeddingResponse):
    dimensions: int = Field(ge=1, le=4096)


class EmbeddingError(Exception):
    """Sanitized upstream error; safe to return through the existing API envelope."""
    def __init__(self, code, message, status_code=502):
        super().__init__(message)
        self.code, self.status_code = code, status_code


class EmbeddingClient:
    """One bounded request per batch, no implicit retry or fallback model.

    An injected httpx client is owned by its caller and intended for tests. Real
    clients always verify TLS and never follow redirects with provider credentials.
    """
    def __init__(self, base_url, model='bge-small-en-v1.5', api_key='', ca_cert='',
                 timeout=15, dimensions=None, client=None):
        parsed = urlparse(base_url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('EMBEDDING_BASE_URL must be an HTTP(S) base URL without credentials/query/fragment.')
        if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1','host.docker.internal'):
            raise ValueError('EMBEDDING_BASE_URL must use HTTPS outside local development.')
        if parsed.path.rstrip('/').endswith('/embeddings'):
            raise ValueError('EMBEDDING_BASE_URL must end before /embeddings, normally at /v1.')
        if not model.strip():
            raise ValueError('EMBEDDING_MODEL must not be blank.')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('EMBEDDING_TIMEOUT_SECONDS must be finite and positive.')
        if dimensions is not None and (type(dimensions) is not int or not 1 <= dimensions <= 4096):
            raise ValueError('EMBEDDING_DIMENSIONS must be between 1 and 4096.')
        self.base_url, self.model = base_url.rstrip('/'), model
        self.api_key, self.timeout, self.dimensions = api_key, timeout, dimensions
        self.owns_client = client is None
        verify = True
        if ca_cert:
            path = Path(os.path.expandvars(os.path.expanduser(str(ca_cert))))
            if not path.is_file():
                raise ValueError('EMBEDDING_CA_CERT must point to an existing CA certificate file.')
            try:
                verify = ssl.create_default_context()
                verify.load_verify_locations(cafile=str(path))
            except (OSError, ssl.SSLError):
                raise ValueError('EMBEDDING_CA_CERT could not be loaded as a PEM CA bundle.') from None
        self.client = client if client is not None else httpx.Client(
            verify=verify, timeout=timeout, follow_redirects=False)

    def close(self):
        if self.owns_client:
            self.client.close()

    def embed_queries(self, queries):
        # Validate even for direct Python callers before any network traffic.
        payload = QueryEmbeddingRequest(input=queries)
        headers = {'Authorization':'Bearer '+self.api_key} if self.api_key else {}
        deadline = time.monotonic() + self.timeout
        try:
            with self.client.stream('POST', self.base_url+'/embeddings',
                                    json={'model':self.model,'input':payload.input},
                                    headers=headers, timeout=self.timeout) as response:
                if response.status_code in (408,429) or response.status_code >= 500:
                    raise EmbeddingError('EMBEDDING_UNAVAILABLE','Embedding provider is temporarily unavailable.',503)
                if response.status_code != 200:
                    raise EmbeddingError('EMBEDDING_REQUEST_REJECTED','Embedding provider rejected the request; check server configuration.')
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise EmbeddingError('EMBEDDING_TIMEOUT','Embedding response exceeded its time budget.',504)
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise EmbeddingError('INVALID_EMBEDDING_RESPONSE','Embedding response exceeds the size limit.')
                    body.extend(chunk)
        except httpx.TimeoutException:
            raise EmbeddingError('EMBEDDING_TIMEOUT','Embedding provider did not respond within the deadline.',504) from None
        except httpx.HTTPError:
            raise EmbeddingError('EMBEDDING_UNAVAILABLE','Embedding provider connection failed.',503) from None
        try:
            parsed = ProviderEmbeddingResponse.model_validate_json(body)
            indices = [entry.index for entry in parsed.data]
            if parsed.model != self.model or sorted(indices) != list(range(len(payload.input))):
                raise ValueError('Model/count/index mismatch.')
            dimensions = len(parsed.data[0].embedding)
            if self.dimensions is not None and dimensions != self.dimensions:
                raise ValueError('Vector dimensions do not match the configured index contract.')
            for entry in parsed.data:
                if len(entry.embedding) != dimensions or not any(entry.embedding):
                    raise ValueError('Inconsistent dimensions or a zero vector.')
            return QueryEmbeddingResponse(
                object='list',model=parsed.model,dimensions=dimensions,
                data=sorted(parsed.data,key=lambda entry:entry.index),usage=parsed.usage)
        except (ValidationError, ValueError):
            raise EmbeddingError('INVALID_EMBEDDING_RESPONSE','Embedding provider returned an invalid vector response.') from None
