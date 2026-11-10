import copy
import json
import re
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from pydantic import ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .contracts import (Limits, AgentState, ControlledStop, RetryableModelError,
                        PermanentModelError, parse_json, call_with_timeout, Plan, Draft, Critique)
from .tools import TOOL_SCHEMAS, tool_attempt
from .models import build_messages

class RegulatoryAgent:
    """Single-process notebook service. One active request per conversation at a time."""
    def __init__(self, model, tools, db_path='agent_state.sqlite', limits=None):
        self.model, self.tools = model, tools
        self.limits = limits or Limits()
        self._closed = False
        self._lock = threading.Lock()
        self.connection = sqlite3.connect(str(db_path), check_same_thread=False)
        self.saver = SqliteSaver(self.connection)
        graph = StateGraph(AgentState)
        for name in ('planner','executor','critic','finish'):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, 'planner')
        graph.add_conditional_edges('planner', lambda s:s['route'],
                                    {'executor':'executor','finish':'finish'})
        graph.add_conditional_edges('executor', lambda s:s['route'],
                                    {'critic':'critic','finish':'finish'})
        graph.add_conditional_edges('critic', lambda s:s['route'],
                                    {'planner':'planner','finish':'finish'})
        graph.add_edge('finish', END)
        self.graph = graph.compile(checkpointer=self.saver)

    def close(self):
        if not self._closed:
            self.connection.close()
            self._closed = True

    @staticmethod
    def event(s, kind, name, **fields):
        s['trace'].append({'kind':kind,'name':name,'step':s['steps'],
                           'elapsed_ms':round((time.time()-s['started_at'])*1000), **fields})

    def remaining_seconds(self, s):
        remaining = self.limits.query_seconds - (time.time()-s['started_at'])
        if remaining <= 0:
            raise ControlledStop('DEADLINE', 'Query deadline reached.')
        return min(self.limits.timeout_seconds, remaining)

    def take_step(self, s):
        self.remaining_seconds(s)
        if s['steps'] >= self.limits.max_steps:
            raise ControlledStop('STEP_CAP', 'Maximum model/tool attempts reached.')
        s['steps'] += 1

    def model_json(self, s, role, payload, schema):
        # Reserve an intentionally conservative byte-based input allowance plus output cap.
        # Failed/no-usage calls retain the FULL reservation; no unmetered SDK retries.
        for attempt in range(2):
            request_payload = dict(payload)
            if attempt:
                request_payload['repair_instruction'] = 'Previous response failed. Return valid schema JSON only.'
            messages = build_messages(role, request_payload, schema)
            reservation = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 512 + self.limits.max_output_tokens
            if s['tokens_charged'] + reservation > self.limits.token_budget:
                raise ControlledStop('TOKEN_CAP', 'Insufficient token allowance for another model call.')
            self.take_step(s)
            s['tokens_charged'] += reservation
            start = time.monotonic()
            try:
                raw, usage = call_with_timeout(
                    lambda:self.model.complete(role, request_payload, schema,
                                               self.limits.max_output_tokens, self.remaining_seconds(s)),
                    self.remaining_seconds(s))
                if usage is not None:
                    if not isinstance(usage, int) or usage < 0:
                        raise PermanentModelError('Invalid provider usage metadata.')
                    s['tokens_charged'] += usage - reservation
                    if usage > reservation or s['tokens_charged'] > self.limits.token_budget:
                        raise ControlledStop('PROVIDER_USAGE_OVERRUN', 'Provider exceeded reserved usage; stopping.')
                parsed = parse_json(raw, schema)
                self.event(s,'model',role,ok=True,attempt=attempt+1,
                           latency_ms=round((time.monotonic()-start)*1000),
                           tokens=usage if usage is not None else reservation,
                           accounting='provider' if usage is not None else 'reserved_upper_allowance')
                return parsed
            except (json.JSONDecodeError, ValidationError, TypeError):
                self.event(s,'model',role,ok=False,code='INVALID_MODEL_OUTPUT',attempt=attempt+1)
            except (TimeoutError, ConnectionError, RetryableModelError):
                self.event(s,'model',role,ok=False,code='MODEL_UNAVAILABLE',attempt=attempt+1)
            except PermanentModelError:
                raise ControlledStop('MODEL_CONFIGURATION', 'Check endpoint, model ID and capability settings.') from None
            except ControlledStop:
                raise
            except Exception:
                raise ControlledStop('MODEL_FAILURE', 'Model request failed; internal details suppressed.') from None
        raise ControlledStop('MODEL_ATTEMPTS_EXHAUSTED', 'Model failed after one retry/repair.')

    def run_tool(self, s, name, arguments):
        for attempt in range(2):
            self.take_step(s)
            start = time.monotonic()
            result = tool_attempt(self.tools, name, arguments, self.remaining_seconds(s))
            self.event(s,'tool',name,ok=result.ok,attempt=attempt+1,
                       latency_ms=round((time.monotonic()-start)*1000),
                       code=None if result.ok else result.error.code)
            if result.ok:
                return result.data
            if not result.error.retryable or attempt == 1:
                raise ControlledStop(result.error.code, result.error.message)
        raise AssertionError('Unreachable')

    def stop(self, s, exc):
        self.event(s,'control','stop',code=exc.code)
        s.update(status='abstained', route='finish',
                 answer={'claims':[], 'limitations':[exc.message], 'message':'I cannot verify an answer from the available evidence.'})
        return s

    def planner(self, s):
        try:
            plan = self.model_json(s,'planner',{
                'question':s['question'], 'history':s['history'][-6:],
                'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'],
                'feedback':s['feedback'], 'previous_plan':s['plan'],
                'remaining_attempts':self.limits.max_steps-s['steps']}, Plan)
            if plan.action != 'answer':
                s.update(plan=plan.model_dump(), status='clarify' if plan.action=='clarify' else 'refused',
                         route='finish', answer={'claims':[], 'limitations':[], 'message':plan.message})
                return s
            if not plan.authority or not plan.query.strip():
                raise ControlledStop('INVALID_PLAN', 'Planner did not identify an authority and search query.')
            if not s['revision'] and plan.tools != list(TOOL_SCHEMAS):
                raise ControlledStop('INVALID_PLAN', 'Initial retrieval must search, fetch and inspect status in order.')
            if len(set(plan.tools)) != len(plan.tools) or not plan.tools:
                raise ControlledStop('INVALID_PLAN', 'Tools must be unique and nonempty.')
            if plan.tools != [name for name in TOOL_SCHEMAS if name in plan.tools]:
                raise ControlledStop('INVALID_PLAN', 'Tool dependencies must run in order.')
            signature = json.dumps(plan.model_dump(), sort_keys=True)
            if signature in s['seen_actions']:
                raise ControlledStop('REPEATED_ACTION', 'Identical plan produced no progress; stopping.')
            s['seen_actions'].append(signature)
            # A changed authority cannot reuse evidence from an earlier retrieval.
            if s['plan'].get('authority') not in (None, plan.authority):
                if plan.tools != list(TOOL_SCHEMAS):
                    raise ControlledStop('INVALID_PLAN', 'Changed authority requires fresh search, passages and status.')
                s['evidence'], s['document_status'] = [], []
            s.update(plan=plan.model_dump(), route='executor')
            return s
        except ControlledStop as exc:
            return self.stop(s, exc)

    def executor(self, s):
        try:
            plan = s['plan']
            ids = list(dict.fromkeys(p['document_id'] for p in s['evidence']))
            for name in plan['tools']:
                if name == 'search_documents':
                    data = self.run_tool(s, name, {'query':plan['query'], 'authority':plan['authority'],
                                                   'fiscal_year':s['fiscal_year']})
                    ids = [h['document_id'] for h in data['hits']]
                    s['evidence'], s['document_status'] = [], []
                    if not ids:
                        raise ControlledStop('RETRIEVAL_MISS', 'No matching documents in this corpus snapshot.')
                elif name == 'fetch_passages':
                    if not ids:
                        raise ControlledStop('INVALID_PLAN', 'No retrieved document IDs to fetch.')
                    s['evidence'] = self.run_tool(s, name, {'document_ids':ids, 'query':plan['query']})['passages']
                    if any(p['document_id'] not in ids or p['authority'] != plan['authority'] for p in s['evidence']):
                        raise ControlledStop('INVALID_TOOL_OUTPUT', 'Fetched evidence does not match the selected documents/authority.')
                else:
                    if not ids:
                        raise ControlledStop('INVALID_PLAN', 'No document IDs for status lookup.')
                    s['document_status'] = self.run_tool(s, name, {
                        'document_ids':ids,'as_of':s['as_of'],'fiscal_year':s['fiscal_year']})['documents']
            applicable = {d['document_id'] for d in s['document_status'] if d['status']=='applicable'}
            if not s['evidence'] or not any(p['document_id'] in applicable for p in s['evidence']):
                raise ControlledStop('APPLICABILITY_UNKNOWN', 'No evidence is verified applicable to the supplied date/year.')
            draft = self.model_json(s,'executor',{
                'question':s['question'], 'history':s['history'][-3:],
                'as_of':s['as_of'],'fiscal_year':s['fiscal_year'],
                'evidence':s['evidence'],'document_status':s['document_status'],
                'feedback':s['feedback']}, Draft)
            s.update(draft=draft.model_dump(), route='critic')
            return s
        except ControlledStop as exc:
            return self.stop(s, exc)

    def citation_issues(self, s):
        evidence = {p['citation_id']:p for p in s['evidence']}
        applicable = {d['document_id'] for d in s['document_status'] if d['status']=='applicable'}
        issues = []
        claims = s['draft'].get('claims', [])
        if not claims:
            issues.append('Draft contains no supported claims.')
        for claim in claims:
            for citation in claim['citations']:
                if citation not in evidence:
                    issues.append('Unknown citation ID: '+citation)
                elif evidence[citation]['document_id'] not in applicable:
                    issues.append('Citation is not verified applicable: '+citation)
        return issues

    def critic(self, s):
        try:
            issues = self.citation_issues(s)
            verdict = self.model_json(s,'critic',{
                'question':s['question'],'as_of':s['as_of'],'fiscal_year':s['fiscal_year'],
                'draft':s['draft'],'evidence':s['evidence'],
                'document_status':s['document_status'], 'deterministic_issues':issues}, Critique)
            self.event(s,'review','verdict',decision=verdict.decision,
                       deterministic_issue_count=len(issues))
            if verdict.decision=='accept' and not issues:
                s.update(status='answered', answer=copy.deepcopy(s['draft']), route='finish')
                # Source metadata comes from the tool, never a model-generated URL.
                cited = {c for claim in s['answer']['claims'] for c in claim['citations']}
                s['answer']['sources'] = [p for p in s['evidence'] if p['citation_id'] in cited]
                return s
            if verdict.decision=='clarify':
                s.update(status='clarify', route='finish', answer={
                    'claims':[], 'limitations':[], 'message':' '.join(verdict.issues) or 'Please clarify the applicable date/year and entity.'})
                return s
            if (verdict.decision=='revise' or issues) and s['revision'] < self.limits.max_revisions:
                # Minimum useful revision: planner + one tool + draft + critic.
                if self.limits.max_steps - s['steps'] < 4:
                    raise ControlledStop('STEP_CAP', 'Insufficient steps for another verified revision.')
                s.update(revision=s['revision']+1, feedback=issues+verdict.issues, route='planner')
                return s
            raise ControlledStop('INSUFFICIENT_EVIDENCE', 'Critic could not verify the answer within the revision limit.')
        except ControlledStop as exc:
            return self.stop(s, exc)

    def finish(self, s):
        # Store only released answers in bounded conversation memory, never rejected drafts.
        entry = {'question':s['question'][:1500], 'authority':s['plan'].get('authority'),
                 'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'], 'status':s['status'],
                 'answer':json.dumps(s['answer'], ensure_ascii=False)[:2000]}
        s['history'] = (s['history']+[entry])[-6:]
        self.event(s,'control','finished',status=s['status'], tokens_charged=s['tokens_charged'])
        return s

    def ask(self, thread_id, question, *, as_of=None, fiscal_year=None):
        if self._closed:
            raise ValueError('Agent is closed.')
        if not isinstance(thread_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',thread_id):
            raise ValueError('Use a 1–100 character alphanumeric conversation ID.')
        if not isinstance(question,str) or not question.strip() or len(question)>4000:
            raise ValueError('Question must contain 1–4000 characters.')
        if fiscal_year is not None and not re.fullmatch(r'\d{4}-\d{2}', fiscal_year):
            raise ValueError('Financial year must use YYYY-YY.')
        if as_of is not None:
            date.fromisoformat(as_of)
        with self._lock:
            config = {'configurable':{'thread_id':thread_id}, 'recursion_limit':20}
            previous = self.graph.get_state(config).values or {}
            history = previous.get('history', [])
            # Follow-ups inherit explicit date/year context, not stale tool evidence or counters.
            resolved_date = as_of or (history[-1]['as_of'] if history else date.today().isoformat())
            resolved_year = fiscal_year or (history[-1].get('fiscal_year') if history else None)
            state = AgentState(
                question=question.strip(), as_of=resolved_date, fiscal_year=resolved_year,
                history=history, plan={}, evidence=[], document_status=[], draft={}, feedback=[],
                answer={}, status='running', revision=0, steps=0, tokens_charged=0,
                trace=[], seen_actions=[], started_at=time.time(), route='planner')
            return self.graph.invoke(state, config)


def show_answer(state):
    print('Status:', state['status'])
    answer = state['answer']
    if answer.get('message'):
        print(answer['message'])
    for claim in answer.get('claims', []):
        print('-', claim['text'], '['+', '.join(claim['citations'])+']')
    for limitation in answer.get('limitations', []):
        print('Limitation:', limitation)
    print('Attempts:', state['steps'], '| Tokens charged:', state['tokens_charged'])


def export_trace(state, path):
    # Metadata only: no API keys, raw user question, document text or reasoning.
    Path(path).write_text('\n'.join(json.dumps(e) for e in state['trace'])+'\n', encoding='utf-8')
