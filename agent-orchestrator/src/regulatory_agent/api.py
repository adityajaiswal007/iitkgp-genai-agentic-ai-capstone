"""Synchronous query execution API; the engine runs in FastAPI's thread pool."""
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent import RegulatoryAgent
from .config import Settings
from .contracts import Claim, Passage
from .models import DemoModel, QwenModel
from .tools import CorpusTools, DEMO_DOCUMENTS, HttpCorpusTools


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')
    as_of: date | None = None
    fiscal_year: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')
    include_trace: bool = False

    @field_validator('question')
    @classmethod
    def trim_question(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Question must not be blank.')
        return value

    @field_validator('as_of', mode='before')
    @classmethod
    def parse_date(cls, value):
        if value is None or isinstance(value, date):
            return value
        if isinstance(value, str) and len(value)==10:
            return date.fromisoformat(value)
        raise ValueError('Use a YYYY-MM-DD date.')

    @field_validator('fiscal_year')
    @classmethod
    def consecutive_years(cls, value):
        if value is not None and (int(value[:4])+1)%100 != int(value[-2:]):
            raise ValueError('Use consecutive financial years, such as 2025-26.')
        return value


class Answer(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    message: str = ''
    sources: list[Passage] = Field(default_factory=list)


class Metrics(BaseModel):
    steps: int
    tokens_charged: int
    revisions: int
    latency_ms: float


class ExecutionResponse(BaseModel):
    execution_id: str
    conversation_id: str
    status: Literal['answered','clarify','refused','abstained']
    mode: Literal['demo','qwen']
    corpus: Literal['synthetic','retrieval'] = 'synthetic'
    answer: Answer
    answer_text: str
    metrics: Metrics
    trace: list[dict] | None = None


class HealthResponse(BaseModel):
    status: Literal['ok'] = 'ok'
    mode: Literal['demo','qwen']
    corpus: Literal['synthetic','retrieval'] = 'synthetic'


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_response(status, code, message):
    return JSONResponse(status_code=status, content={'error':{'code':code,'message':message}})


class BodyLimitMiddleware:
    """Bound streamed POST bodies before JSON parsing (64 KiB, including chunked requests)."""
    def __init__(self, app, max_bytes=65536):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope,receive,send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type']=='http.disconnect':
                return
            body.extend(message.get('body',b''))
            if len(body)>self.max_bytes:
                return await error_response(413,'REQUEST_TOO_LARGE','Request body exceeds 64 KiB.')(scope,receive,send)
            if not message.get('more_body',False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,bounded_receive,send)


def render_answer(answer):
    lines = [answer.message] if answer.message else []
    lines.extend(f"{claim.text} [{', '.join(claim.citations)}]" for claim in answer.claims)
    lines.extend('Limitation: '+text for text in answer.limitations)
    return '\n\n'.join(lines)


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.from_env()
        config.database_path.parent.mkdir(parents=True,exist_ok=True)
        model = DemoModel() if config.mode=='demo' else QwenModel()
        app.state.settings = config
        app.state.execution_lock = threading.Lock()
        tools = (HttpCorpusTools(config.retrieval_base_url, config.retrieval_api_key, config.timeout_seconds)
                 if config.retrieval_base_url else CorpusTools(DEMO_DOCUMENTS))
        app.state.corpus = 'retrieval' if config.retrieval_base_url else 'synthetic'
        app.state.agent = RegulatoryAgent(model,tools,config.database_path,config.limits())
        try:
            yield
        finally:
            app.state.agent.close()
            if isinstance(tools, HttpCorpusTools):
                tools.close()
            if isinstance(model,QwenModel):
                model.client.close()

    app = FastAPI(title='Regulatory Agent API', version='0.1.0', lifespan=lifespan,
                  description='Read-only planner/executor/critic with optional HTTP hybrid retrieval.')
    app.add_middleware(BodyLimitMiddleware)
    key_header = APIKeyHeader(name='X-API-Key', auto_error=False)

    def authorize(request: Request, supplied: Annotated[str | None, Depends(key_header)]):
        expected = request.app.state.settings.api_key
        if expected and (not supplied or not secrets.compare_digest(supplied.encode(), expected.encode())):
            raise HTTPException(401,detail={'code':'UNAUTHORIZED','message':'A valid X-API-Key is required.'})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's default errors can echo raw questions or credentials from the body.
        return error_response(422,'VALIDATION_ERROR','Invalid request. See /docs for the required schema.')

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail,dict) else {'code':'HTTP_ERROR','message':str(exc.detail)}
        return error_response(exc.status_code,detail['code'],detail['message'])

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return error_response(500,'INTERNAL_ERROR','Execution failed; internal details suppressed.')

    @app.get('/health', response_model=HealthResponse)
    def health(request: Request):
        # This reports local startup, not a successful live-model connectivity probe.
        return HealthResponse(mode=request.app.state.settings.mode, corpus=request.app.state.corpus)

    @app.post('/v1/executions', response_model=ExecutionResponse,
              dependencies=[Depends(authorize)], responses={
                  status:{'model':ErrorResponse} for status in (401,409,413,422,500)},
              summary='Execute one query through the planner, tools, and critic')
    def execute(payload: ExecutionRequest, request: Request):
        if not request.app.state.execution_lock.acquire(blocking=False):
            raise HTTPException(409,detail={'code':'SERVICE_BUSY','message':'An execution is running. Retry after it completes.'})
        try:
            started = time.monotonic()
            conversation_id = payload.conversation_id or str(uuid.uuid4())
            state = request.app.state.agent.ask(
                conversation_id,payload.question,
                as_of=payload.as_of.isoformat() if payload.as_of else None,
                fiscal_year=payload.fiscal_year)
            answer = Answer.model_validate(state['answer'])
            return ExecutionResponse(
                execution_id=str(uuid.uuid4()), conversation_id=conversation_id,
                status=state['status'], mode=request.app.state.settings.mode, corpus=request.app.state.corpus,
                answer=answer, answer_text=render_answer(answer),
                metrics=Metrics(steps=state['steps'],tokens_charged=state['tokens_charged'],
                                revisions=state['revision'],latency_ms=round((time.monotonic()-started)*1000,2)),
                trace=state['trace'] if payload.include_trace else None)
        finally:
            request.app.state.execution_lock.release()

    return app


app = create_app()
