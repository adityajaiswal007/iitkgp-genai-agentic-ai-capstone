"""Environment configuration for the retrieval service."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("../data")
    index_dir: Path = Path("index")
    api_key: str = field(default="", repr=False)
    include_out_of_scope: bool = False
    auto_build: bool = True
    ocr: bool = True

    # Chunking and passage limits. The agent shows the model (and number-checks) only
    # the first 1800 characters of each passage, so max + overlap must stay below that.
    chunk_target_chars: int = 1200
    chunk_max_chars: int = 1500
    chunk_overlap_chars: int = 250
    fetch_max_passages: int = 8

    # Dense retrieval (optional): any OpenAI-compatible /embeddings endpoint.
    embedding_base_url: str = ""
    embedding_api_key: str = field(default="", repr=False)
    embedding_model: str = ""
    embedding_ca_cert: str = ""
    embedding_batch_size: int = 32
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""

    # Reranking (optional): a /rerank endpoint in the Cohere/Jina/vLLM/TEI style.
    rerank_base_url: str = ""
    rerank_api_key: str = field(default="", repr=False)
    rerank_model: str = ""
    rerank_ca_cert: str = ""
    rerank_candidates: int = 30

    request_timeout: float = 20.0

    def __post_init__(self):
        if self.chunk_max_chars + self.chunk_overlap_chars + 2 > 1800:
            raise ValueError("RAG_CHUNK_MAX_CHARS + RAG_CHUNK_OVERLAP_CHARS must stay below 1800, "
                             "the passage length the agent shows the model.")

    @property
    def dense_enabled(self) -> bool:
        return bool(self.embedding_base_url and self.embedding_model)

    @property
    def rerank_enabled(self) -> bool:
        return bool(self.rerank_base_url)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.getenv("RAG_DATA_DIR", "../data")),
            index_dir=Path(os.getenv("RAG_INDEX_DIR", "index")),
            api_key=os.getenv("RAG_API_KEY", ""),
            include_out_of_scope=_bool("RAG_INCLUDE_OUT_OF_SCOPE", False),
            auto_build=_bool("RAG_AUTO_BUILD", True),
            ocr=_bool("RAG_OCR", True),
            chunk_target_chars=int(os.getenv("RAG_CHUNK_TARGET_CHARS", "1200")),
            chunk_max_chars=int(os.getenv("RAG_CHUNK_MAX_CHARS", "1500")),
            chunk_overlap_chars=int(os.getenv("RAG_CHUNK_OVERLAP_CHARS", "250")),
            fetch_max_passages=int(os.getenv("RAG_FETCH_MAX_PASSAGES", "8")),
            embedding_base_url=os.getenv("EMBEDDING_BASE_URL", "").rstrip("/"),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", ""),
            embedding_ca_cert=os.getenv("EMBEDDING_CA_CERT", ""),
            embedding_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")),
            embedding_query_prefix=os.getenv("EMBEDDING_QUERY_PREFIX", ""),
            embedding_document_prefix=os.getenv("EMBEDDING_DOCUMENT_PREFIX", ""),
            rerank_base_url=os.getenv("RERANK_BASE_URL", "").rstrip("/"),
            rerank_api_key=os.getenv("RERANK_API_KEY", ""),
            rerank_model=os.getenv("RERANK_MODEL", ""),
            rerank_ca_cert=os.getenv("RERANK_CA_CERT", ""),
            rerank_candidates=int(os.getenv("RERANK_CANDIDATES", "30")),
            request_timeout=float(os.getenv("RAG_REQUEST_TIMEOUT", "20")),
        )
