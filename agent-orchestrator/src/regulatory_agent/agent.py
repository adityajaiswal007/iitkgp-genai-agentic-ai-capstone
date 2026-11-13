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
                        PermanentModelError, parse_json, call_with_timeout, Plan, Draft, Critique, ContextResolution)
from .tools import TOOL_SCHEMAS, tool_attempt
from .attachments import AttachmentTools
from .models import build_messages, history_messages

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
        for name in ('resolve_context','planner','executor','critic','finish'):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, 'resolve_context')
        graph.add_conditional_edges('resolve_context', lambda s:s['route'],
                                    {'planner':'planner','finish':'finish'})
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
            tools = AttachmentTools(self.tools, s['attachment_passages'], s['current_attachment_ids']) if s['attachment_passages'] else self.tools
            result = tool_attempt(tools, name, arguments, self.remaining_seconds(s))
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

    def resolve_context(self, s):
        if not s['messages'] or (s['current_attachment_ids'] and not re.search(r'\b(their|them|his|her|these people|those people)\b', s['question'], re.I)):
            s['route'] = 'planner'
            return s
        try:
            resolved = self.model_json(s, 'contextualizer', {
                'question':s['question'], 'messages':s['messages']}, ContextResolution)
            referents = list(dict.fromkeys(resolved.referents))
            prior_answers = '\n'.join(m['content'] for m in s['messages'] if m['role']=='assistant').casefold()
            if any(name.casefold() not in prior_answers for name in referents):
                raise ControlledStop('INVALID_CONTEXT', 'Could not verify the referenced entities in this conversation.')
            # Preserve the user's request verbatim; bind identities without letting a rewrite
            # change the requested attribute or narrow a plural reference to a single region.
            if referents:
                s['resolved_question'] = s['question']+'\nReferenced entities: '+', '.join(referents)
            s['route'] = 'planner'
            return s
        except ControlledStop as exc:
            return self.stop(s, exc)

    @staticmethod
    def upload_catalog(s):
        return list({p['document_id']:{'document_id':p['document_id'], 'name':p['title'], 'uploaded_this_turn':p['document_id'] in s['current_attachment_ids']}
                     for p in s['attachment_passages']}.values())

    def model_question(self, s):
        current = [file['name'] for file in self.upload_catalog(s) if file['uploaded_this_turn']]
        return s['resolved_question']+('\nFiles attached to the CURRENT message: '+', '.join(current) if current else '')

    def planner(self, s):
        try:
            plan = self.model_json(s,'planner',{
                'question':self.model_question(s), 'history':s['history'], 'messages':s['messages'],
                'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'],
                'feedback':s['feedback'], 'previous_plan':s['plan'],
                'revision':s['revision'], 'uploaded_files':self.upload_catalog(s),
                'remaining_attempts':self.limits.max_steps-s['steps']}, Plan)
            if plan.action != 'answer':
                s.update(plan=plan.model_dump(), status='clarify' if plan.action=='clarify' else 'refused',
                         route='finish', answer={'claims':[], 'limitations':[], 'message':plan.message})
                return s
            if plan.authority == 'UPLOAD' and not s['attachment_passages']:
                raise ControlledStop('INVALID_PLAN', 'This conversation has no uploaded evidence.')
            if not plan.authority or not plan.query.strip():
                raise ControlledStop('INVALID_PLAN', 'Planner did not identify an authority and search query.')
            if not s['revision']:
                if s['resolved_question'] != s['question']:
                    bound = s['resolved_question']
                    if len(bound) <= 1500:
                        plan.query = bound
                    else:
                        entities = bound.rsplit('\nReferenced entities: ',1)[-1]
                        plan.query = plan.query[:1500-len(entities)-1]+' '+entities
                # Every new user turn has empty evidence. The engine owns this dependency
                # chain even if the model mistakes a conversational follow-up for a revision.
                plan.tools = list(TOOL_SCHEMAS)
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
            previous_snapshots = {(p['document_id'], p['corpus_snapshot']) for p in s['evidence']}
            checked_ids = {d['document_id'] for d in s['document_status']}
            for name in plan['tools']:
                if name == 'search_documents':
                    data = self.run_tool(s, name, {'query':plan['query'], 'authority':plan['authority'],
                                                   'fiscal_year':s['fiscal_year'], 'limit':5 if plan['authority']=='UPLOAD' else 3})
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
                    current_snapshots = {(p['document_id'], p['corpus_snapshot']) for p in s['evidence']}
                    # Re-fetching the same immutable catalog snapshot does not invalidate its status.
                    # Save the remaining attempt for the critic; changed snapshots are always rechecked.
                    if (s['revision'] and 'search_documents' not in plan['tools']
                            and current_snapshots and current_snapshots <= previous_snapshots
                            and set(ids) <= checked_ids):
                        self.event(s, 'control', 'reused_document_status')
                        continue
                    s['document_status'] = self.run_tool(s, name, {
                        'document_ids':ids,'as_of':s['as_of'],'fiscal_year':s['fiscal_year']})['documents']
            applicable = {d['document_id'] for d in s['document_status'] if d['status']=='applicable'}
            if not s['evidence'] or not any(p['document_id'] in applicable for p in s['evidence']):
                raise ControlledStop('APPLICABILITY_UNKNOWN', 'No evidence is verified applicable to the supplied date/year.')
            draft = self.model_json(s,'executor',{
                'question':self.model_question(s), 'history':s['history'], 'messages':[] if s['current_attachment_ids'] else s['messages'],
                'as_of':s['as_of'],'fiscal_year':s['fiscal_year'],
                'evidence':s['evidence'],'document_status':s['document_status'],
                'feedback':s['feedback'], 'uploaded_files':self.upload_catalog(s)}, Draft)
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
        asks_current = re.search(r'\b(newly|just)\s+(attached|uploaded)\b|\b(the|this)\s+attached\s+(file|document|image|pdf|csv)\b', s['question'], re.I)
        if s['current_attachment_ids'] and asks_current and s['plan'].get('authority')=='UPLOAD' and claims:
            cited_docs = {evidence[c]['document_id'] for claim in claims for c in claim['citations'] if c in evidence}
            if not cited_docs.intersection(s['current_attachment_ids']):
                issues.append('The answer must cite the file(s) attached to this message, not only an older upload.')
        return issues

    def critic(self, s):
        try:
            issues = self.citation_issues(s)
            verdict = self.model_json(s,'critic',{
                'question':self.model_question(s), 'messages':[] if s['current_attachment_ids'] else s['messages'],
                'as_of':s['as_of'],'fiscal_year':s['fiscal_year'],
                'draft':s['draft'],'evidence':s['evidence'],
                'document_status':s['document_status'], 'deterministic_issues':issues, 'uploaded_files':self.upload_catalog(s)}, Critique)
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
        # Store complete released answers without copying retrieved passage bodies into memory.
        entry = {'question':s['question'], 'authority':s['plan'].get('authority'),
                 'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'], 'status':s['status'],
                 'answer':json.dumps({k:v for k,v in s['answer'].items() if k != 'sources'}, ensure_ascii=False)}
        s['history'] = s['history']+[entry]
        s['messages'] = s['messages']+history_messages([entry])
        self.event(s,'control','finished',status=s['status'], tokens_charged=s['tokens_charged'])
        return s

    def ask(self, thread_id, question, *, as_of=None, fiscal_year=None, messages=None, attachment_passages=None, current_attachment_ids=None, started_at=None):
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
                question=question.strip(), resolved_question=question.strip(), as_of=resolved_date, fiscal_year=resolved_year,
                history=history, messages=messages if messages is not None else previous.get('messages', history_messages(history)),
                attachment_passages=attachment_passages if attachment_passages is not None else previous.get('attachment_passages', []),
                current_attachment_ids=current_attachment_ids or [], plan={}, evidence=[], document_status=[], draft={}, feedback=[],
                answer={}, status='running', revision=0, steps=0, tokens_charged=0,
                trace=[], seen_actions=[], started_at=started_at if started_at is not None else time.time(), route='planner')
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
