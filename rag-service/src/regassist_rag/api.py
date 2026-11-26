"""HTTP retrieval API.

Implements the contract that agent-orchestrator's HttpCorpusTools calls:
  POST /v1/tools/search_documents
  POST /v1/tools/fetch_passages
  POST /v1/tools/lookup_document_status
Responses contain exactly the fields of the agent's SearchData / FetchData /
StatusData schemas (the agent validates them strictly).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .index import Retriever

log = logging.getLogger(__name__)

Authority = Literal["RBI", "SEBI", "TAX"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(_Strict):
    query: str = Field(min_length=1, max_length=1500)
    authority: Authority
    limit: int = Field(default=3, ge=1, le=5)
    fiscal_year: str | None = None


class FetchRequest(_Strict):
    document_ids: list[str] = Field(min_length=1, max_length=5)
    query: str | None = Field(default=None, min_length=1, max_length=1500)


class StatusRequest(_Strict):
    document_ids: list[str] = Field(min_length=1, max_length=5)
    as_of: str
    fiscal_year: str | None = None


class ChunkSearchRequest(_Strict):
    query: str = Field(min_length=1, max_length=1500)
    authority: Authority | None = None
    document_ids: list[str] | None = Field(default=None, max_length=20)
    top_k: int = Field(default=10, ge=1, le=50)


def create_app(settings: Settings | None = None, retriever: Retriever | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.retriever = retriever or Retriever.load_or_build(settings)
        log.info("Retrieval index %s loaded", app.state.retriever.snapshot)
        yield
        app.state.retriever.close()

    app = FastAPI(title="RegAssist Retrieval API", lifespan=lifespan)

    def authorize(x_api_key: Annotated[str, Header()] = "") -> None:
        if settings.api_key and not compare_digest(x_api_key.encode(), settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid retrieval API key.")

    def get_retriever(request: Request) -> Retriever:
        return request.app.state.retriever

    tools = APIRouter(prefix="/v1/tools", dependencies=[Depends(authorize)])
    extra = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])

    @app.get("/health")
    def health(request: Request):
        r: Retriever = request.app.state.retriever
        return {
            "status": "ok",
            "corpus": "regassist",
            "snapshot": r.snapshot,
            "documents": r.info["documents_by_authority"],
            "chunks": len(r.chunks),
            "dense": r.embeddings is not None,
            "rerank": r.reranker is not None,
        }

    @tools.post("/search_documents")
    def search_documents(body: SearchRequest, r: Retriever = Depends(get_retriever)):
        return r.search_documents(body.query, body.authority, body.limit, body.fiscal_year)

    @tools.post("/fetch_passages")
    def fetch_passages(body: FetchRequest, r: Retriever = Depends(get_retriever)):
        try:
            return r.fetch_passages(body.document_ids, body.query)
        except KeyError:
            raise HTTPException(status_code=404, detail="Document was not found.") from None

    @tools.post("/lookup_document_status")
    def lookup_document_status(body: StatusRequest, r: Retriever = Depends(get_retriever)):
        try:
            return r.lookup_document_status(body.document_ids, body.as_of, body.fiscal_year)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid as_of date.") from None

    @extra.get("/catalog")
    def catalog(r: Retriever = Depends(get_retriever)):
        return {"snapshot": r.snapshot, "documents": [
            {k: d.to_dict()[k] for k in ("document_id", "authority", "collection", "title", "doc_type",
                                         "effective_from", "effective_to", "superseded_by", "source_url")}
            for d in r.docs]}

    @extra.post("/search_chunks")
    def search_chunks(body: ChunkSearchRequest, r: Retriever = Depends(get_retriever)):
        """Chunk-level results with text, for evaluation and debugging."""
        hits = r.rank_chunks(body.query, body.authority, body.document_ids, body.top_k)
        return {"snapshot": r.snapshot, "hits": [
            {**r.chunks[h.index].to_dict(), "score": round(h.score, 6)} for h in hits]}

    app.include_router(tools)
    app.include_router(extra)
    return app


def _default_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()


app = _default_app()
