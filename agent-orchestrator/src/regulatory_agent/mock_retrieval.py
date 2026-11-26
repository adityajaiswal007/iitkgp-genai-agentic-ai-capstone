"""Local HTTP stub for the orchestrator's document retrieval contract."""

import os
from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from .contracts import CatalogSearchArgs, FetchArgs, FetchData, SearchData, StatusArgs, StatusData
from .tools import CorpusTools, DEMO_DOCUMENTS


def create_mock_app(api_key: str = '') -> FastAPI:
    corpus = CorpusTools(DEMO_DOCUMENTS)
    app = FastAPI(title='Mock Document Retrieval API')

    def authorize(x_api_key: Annotated[str, Header()] = '') -> None:
        if api_key and not compare_digest(x_api_key.encode(), api_key.encode()):
            raise HTTPException(status_code=401, detail='Invalid retrieval API key.')

    tools = APIRouter(prefix='/v1/tools', dependencies=[Depends(authorize)])

    @app.get('/health')
    def health():
        return {'status': 'ok', 'corpus': 'synthetic'}

    @tools.post('/search_documents', response_model=SearchData)
    def search_documents(args: CatalogSearchArgs):
        return corpus.search_documents(args)

    @tools.post('/fetch_passages', response_model=FetchData)
    def fetch_passages(args: FetchArgs):
        try:
            return corpus.fetch_passages(args)
        except KeyError:
            raise HTTPException(status_code=404, detail='Document was not found.') from None

    @tools.post('/lookup_document_status', response_model=StatusData)
    def lookup_document_status(args: StatusArgs):
        try:
            return corpus.lookup_document_status(args)
        except ValueError:
            raise HTTPException(status_code=422, detail='Invalid as_of date.') from None

    app.include_router(tools)
    return app


app = create_mock_app(api_key=os.getenv('RAG_API_KEY', ''))
