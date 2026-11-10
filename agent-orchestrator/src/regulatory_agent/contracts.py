from __future__ import annotations
import copy
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

Authority = Literal['RBI', 'SEBI', 'INCOME_TAX', 'AVL']
ToolName = Literal['search_documents', 'fetch_passages', 'lookup_document_status']

class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class ContextResolution(Schema):
    referents: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=12)


class Plan(Schema):
    action: Literal['answer', 'clarify', 'refuse']
    query: str = Field(default='', max_length=1500)
    authority: Authority | None = None
    message: str = Field(default='', max_length=1000)
    tools: list[ToolName] = Field(default_factory=lambda: [
        'search_documents', 'fetch_passages', 'lookup_document_status'], max_length=3)

class Claim(Schema):
    text: str = Field(min_length=1, max_length=1500)
    citations: list[str] = Field(min_length=1, max_length=6)

class Draft(Schema):
    claims: list[Claim] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=6)

class Critique(Schema):
    decision: Literal['accept', 'revise', 'clarify', 'insufficient_evidence']
    issues: list[str] = Field(default_factory=list, max_length=8)

class SearchArgs(Schema):
    query: str = Field(min_length=1, max_length=1500)
    authority: Authority
    limit: int = Field(default=3, ge=1, le=5)
    fiscal_year: str | None = None

class FetchArgs(Schema):
    document_ids: list[str] = Field(min_length=1, max_length=5)
    query: str | None = Field(default=None, min_length=1, max_length=1500)

class StatusArgs(Schema):
    document_ids: list[str] = Field(min_length=1, max_length=5)
    as_of: str
    fiscal_year: str | None = None

class Passage(Schema):
    citation_id: str
    document_id: str
    authority: Authority
    title: str
    text: str = Field(max_length=6000)
    page: int = Field(ge=1)
    section: str
    source_url: str
    corpus_snapshot: str

class Hit(Schema):
    document_id: str
    score: float

class SearchData(Schema):
    hits: list[Hit] = Field(max_length=5)

class FetchData(Schema):
    passages: list[Passage] = Field(max_length=10)

class DocumentStatus(Schema):
    document_id: str
    status: Literal['applicable', 'not_applicable', 'unknown']
    reason: str

class StatusData(Schema):
    documents: list[DocumentStatus] = Field(max_length=5)

class ToolError(Schema):
    code: str
    message: str
    retryable: bool

class ToolResult(Schema):
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None

@dataclass(frozen=True)
class Limits:
    max_steps: int = 10
    max_revisions: int = 1
    token_budget: int = 48000
    max_output_tokens: int = 900
    timeout_seconds: float = 15
    query_seconds: float = 120

    def __post_init__(self):
        if not 1 <= self.max_steps <= 10 or not 0 <= self.max_revisions <= 1:
            raise ValueError('Use 1–10 steps and 0–1 revisions.')
        if min(self.token_budget, self.max_output_tokens, self.timeout_seconds, self.query_seconds) <= 0:
            raise ValueError('Budgets and deadlines must be positive.')

class AgentState(TypedDict):
    question: str
    resolved_question: str
    as_of: str
    fiscal_year: str | None
    history: list[dict]
    messages: list[dict]
    plan: dict
    evidence: list[dict]
    document_status: list[dict]
    draft: dict
    feedback: list[str]
    answer: dict
    status: str
    revision: int
    steps: int
    tokens_charged: int
    trace: list[dict]
    seen_actions: list[str]
    started_at: float
    route: str

class ControlledStop(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

class RetryableModelError(Exception):
    pass

class PermanentModelError(Exception):
    pass


def parse_json(raw: str, schema):
    # Never execute model output. Accept one JSON object, optionally fenced.
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.S).strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
    return schema.model_validate(json.loads(raw))


def call_with_timeout(fn, seconds):
    # Read-only adapters only. A timed-out Python thread cannot be killed.
    # Production HTTP adapters MUST also configure native connection/read timeouts.
    result_queue = queue.Queue(maxsize=1)
    def worker():
        try:
            result_queue.put((True, fn()))
        except Exception as exc:
            result_queue.put((False, exc))
    threading.Thread(target=worker, daemon=True).start()
    try:
        ok, value = result_queue.get(timeout=seconds)
    except queue.Empty:
        raise TimeoutError('Operation deadline exceeded') from None
    if not ok:
        raise value
    return value
