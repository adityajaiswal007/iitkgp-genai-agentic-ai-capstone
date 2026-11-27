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

Regulator = Literal['RBI', 'SEBI']
Authority = Literal[Regulator, 'UPLOAD']
PlanAuthority = Literal[Authority, 'BOTH']
RefusalCategory = Literal['out_of_scope', 'personal_advice', 'action_request', 'personal_data', 'rule_evasion']
ToolName = Literal['search_documents', 'fetch_passages', 'lookup_document_status']

class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class ContextResolution(Schema):
    referents: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=12)


class Plan(Schema):
    action: Literal['answer', 'clarify', 'refuse']
    query: str = Field(default='', max_length=1500)
    authority: PlanAuthority | None = None
    alternative_queries: list[Annotated[str, Field(min_length=1, max_length=1500)]] = Field(default_factory=list, max_length=2)
    assumptions: list[Annotated[str, Field(max_length=180)]] = Field(default_factory=list, max_length=3)
    refusal_category: RefusalCategory | None = None
    message: str = Field(default='', max_length=1000)
    tools: list[ToolName] = Field(default_factory=lambda: [
        'search_documents', 'fetch_passages', 'lookup_document_status'], max_length=3)

class Claim(Schema):
    text: str = Field(min_length=1, max_length=1500)
    citations: list[str] = Field(min_length=1, max_length=6)

class DraftClaim(Claim):
    kind: Literal['rule', 'condition', 'exception', 'deadline', 'amount', 'definition', 'procedure'] | None = None


class Draft(Schema):
    answer_status: Literal['answered', 'partial', 'not_found'] = 'answered'
    evidence_notes: list[Annotated[str, Field(max_length=180)]] = Field(default_factory=list, max_length=5)
    claims: list[DraftClaim] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=6)

class ReviewChecks(Schema):
    relevance: Literal['pass', 'fail']
    factual_support: Literal['pass', 'fail']
    citation_support: Literal['pass', 'fail']
    completeness: Literal['pass', 'fail']
    applicability: Literal['pass', 'fail']


class Critique(Schema):
    decision: Literal['accept', 'revise', 'clarify', 'insufficient_evidence']
    issues: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=8)
    checks: ReviewChecks | None = None
    revise_target: Literal['planner', 'executor'] | None = None
    clarification_question: str = Field(default='', max_length=300)


class StateSummary(Schema):
    topic: str = Field(default='', max_length=120)
    authority: Literal[Regulator, 'BOTH'] | None = None
    entities: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=5)
    established_facts: list[Annotated[str, Field(max_length=140)]] = Field(default_factory=list, max_length=4)
    open_points: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=3)


class SearchArgs(Schema):
    query: str = Field(min_length=1, max_length=1500)
    authority: Authority
    limit: int = Field(default=3, ge=1, le=5)
    fiscal_year: str | None = None

class CatalogSearchArgs(SearchArgs):
    authority: Regulator


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
    effective_from: str | None = None
    effective_to: str | None = None
    superseded_by: str | None = None


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
    private_input: bool
    resolved_question: str
    topic_index: int | None
    requested_authority: Regulator | None
    as_of: str
    fiscal_year: str | None
    history: list[dict]
    messages: list[dict]
    summary: dict
    attachment_passages: list[dict]
    current_attachment_ids: list[str]
    plan: dict
    evidence: list[dict]
    document_status: list[dict]
    draft: dict
    private_output: bool
    feedback: list[str]
    revise_target: Literal['planner', 'executor'] | None
    answer: dict
    status: str
    revision: int
    steps: int
    token_budget: int
    estimated_tokens: int
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
