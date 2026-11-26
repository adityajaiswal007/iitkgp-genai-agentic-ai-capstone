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
                        PermanentModelError, parse_json, call_with_timeout, Plan, Draft, Critique,
                        ContextResolution, StateSummary)
from .tools import TOOL_SCHEMAS, tool_attempt
from .attachments import AttachmentTools
from .models import REFUSAL_MESSAGES, ROLE_OUTPUT_TOKENS, build_messages, history_messages
from .grounding import (catalog_statuses, contains_private_value, label_evidence, numbers,
                        redact, regulatory_uploads)

ABSTAIN_MESSAGE = 'I could not verify an answer from the available RBI and SEBI evidence.'
TOOL_CHAIN = list(TOOL_SCHEMAS)


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
                                    {'planner':'planner','executor':'executor','finish':'finish'})
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

    def model_json(self, s, role, payload, schema, *, attempts=2):
        output_cap = min(self.limits.max_output_tokens, ROLE_OUTPUT_TOKENS.get(role, self.limits.max_output_tokens))
        request_payload, call_role = payload, role
        for attempt in range(1, attempts + 1):
            reservation = self.model_reservation(call_role, request_payload, schema, output_cap)
            if s['tokens_charged'] + reservation > s['token_budget']:
                self.event(s, 'budget', 'reservation_rejected', role=call_role,
                           required=reservation, remaining=s['token_budget']-s['tokens_charged'])
                raise ControlledStop('TOKEN_CAP', 'Insufficient token allowance for another model call.')
            self.take_step(s)
            s['tokens_charged'] += reservation
            s['estimated_tokens'] += reservation
            start = time.monotonic()
            usage = None
            raw = ''
            try:
                raw, usage = call_with_timeout(
                    lambda:self.model.complete(call_role, request_payload, schema,
                                               output_cap, self.remaining_seconds(s)),
                    self.remaining_seconds(s))
                if usage is not None:
                    if type(usage) is not int or usage < 0:
                        raise PermanentModelError('Invalid provider usage metadata.')
                    s['tokens_charged'] += usage - reservation
                    s['estimated_tokens'] -= reservation
                    if usage > reservation or s['tokens_charged'] > s['token_budget']:
                        raise ControlledStop('PROVIDER_USAGE_OVERRUN', 'Provider exceeded reserved usage; stopping.')
                parsed = parse_json(raw, schema)
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                if isinstance(exc, ValidationError):
                    errors = [
                        redact(f"{'.'.join(map(str, error['loc']))[:100]} ({error['type']})")
                        for error in exc.errors(include_input=False)[:6]
                    ]
                else:
                    errors = ['Invalid JSON object.']
                code = 'INVALID_MODEL_OUTPUT'
                next_role = 'json_repair'
                next_payload = {
                    'original_role':role, 'broken_output':redact(raw[:4000]) if isinstance(raw, str) else '',
                    'validation_errors':errors,
                }
            except (TimeoutError, ConnectionError, RetryableModelError):
                code = 'MODEL_UNAVAILABLE'
                next_role, next_payload = call_role, request_payload
            except PermanentModelError:
                raise ControlledStop('MODEL_CONFIGURATION', 'Check endpoint, model ID and capability settings.') from None
            except ControlledStop:
                raise
            except Exception:
                raise ControlledStop('MODEL_FAILURE', 'Model request failed; internal details suppressed.') from None
            else:
                code = None
            self.event(s, 'model', call_role, ok=code is None, code=code, attempt=attempt,
                       latency_ms=round((time.monotonic()-start)*1000),
                       tokens=usage if usage is not None else reservation,
                       accounting='provider' if usage is not None else 'reserved_upper_allowance')
            if code is None:
                return parsed
            call_role, request_payload = next_role, next_payload
        raise ControlledStop('MODEL_ATTEMPTS_EXHAUSTED', 'Model output could not be verified within the attempt limit.')

    def model_reservation(self, role, payload, schema, output_cap):
        counter = getattr(self.model, 'count_input_tokens', None)
        if counter is None:
            messages = build_messages(role, payload, schema)
            return len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 512 + output_cap
        try:
            input_tokens = counter(role, payload, schema)
            if type(input_tokens) is not int or input_tokens < 0:
                raise ValueError('Invalid tokenizer count.')
        except Exception:
            raise ControlledStop('TOKENIZER_CONFIGURATION',
                                 'Cannot count model tokens. Check the Qwen tokenizer configuration.') from None
        return input_tokens + 32 + output_cap

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
                 answer={'claims':[], 'limitations':[exc.message], 'message':ABSTAIN_MESSAGE})
        return s

    def resolve_context(self, s):
        if s['private_input']:
            s.update(status='refused', route='finish', answer={
                'claims':[], 'limitations':[], 'message':REFUSAL_MESSAGES['personal_data']})
            return s
        if not s['messages'] or (s['current_attachment_ids'] and not re.search(r'\b(their|them|his|her|these people|those people)\b', s['question'], re.I)):
            s['route'] = 'planner'
            return s
        try:
            needs_exact_history = re.search(
                r'(?:their|them|his|her|it|that|those|these|earlier|first|previously)', s['question'], re.I)
            messages = s['messages'] if not s['summary'] or needs_exact_history else s['messages'][-4:]
            resolved = self.model_json(s, 'contextualizer', {
                'question':s['question'], 'messages':messages, 'summary':s['summary'],
            }, ContextResolution)
            referents = list(dict.fromkeys(resolved.referents))
            prior_answers = '\n'.join(m['content'] for m in s['messages'] if m['role']=='assistant').casefold()
            if any(contains_private_value(name) or name.casefold() not in prior_answers for name in referents):
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
            plan = self.model_json(s, 'planner', {
                'question':self.model_question(s), 'history':s['history'],
                'messages':s['messages'][-4:] if s['summary'] else s['messages'],
                'summary':s['summary'], 'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'],
                'feedback':s['feedback'], 'previous_plan':s['plan'],
                'revision':s['revision'], 'uploaded_files':self.upload_catalog(s),
                'remaining_attempts':self.limits.max_steps-s['steps']}, Plan)
            if plan.action != 'answer':
                message = (REFUSAL_MESSAGES[plan.refusal_category or 'out_of_scope'] if plan.action == 'refuse'
                           else redact(plan.message.strip()) or 'Which RBI or SEBI rule or entity do you mean?')
                s.update(plan={'action':plan.action}, status='clarify' if plan.action=='clarify' else 'refused',
                         route='finish', answer={'claims':[], 'limitations':[], 'message':message})
                return s
            if plan.authority == 'UPLOAD' and not s['attachment_passages']:
                raise ControlledStop('INVALID_PLAN', 'No RBI or SEBI attachment evidence is available.')
            if not plan.authority or not plan.query.strip() or plan.refusal_category:
                raise ControlledStop('INVALID_PLAN', 'Planner did not supply a valid authority and search query.')
            plan.query = redact(plan.query.strip())
            if not s['revision']:
                if s['resolved_question'] != s['question']:
                    entities = s['resolved_question'].rsplit('\nReferenced entities: ', 1)[-1]
                    if len(entities) >= 1499:
                        raise ControlledStop('INVALID_CONTEXT', 'The referenced entities exceed the search query limit.')
                    plan.query = (s['resolved_question'] if len(s['resolved_question']) <= 1500
                                  else plan.query[:1499-len(entities)]+' '+entities)
                    plan.alternative_queries = []
                plan.tools = TOOL_CHAIN.copy()
            seen_queries = {plan.query.casefold()}
            alternatives = []
            for query in plan.alternative_queries:
                query = redact(query.strip())
                if query and query.casefold() not in seen_queries:
                    seen_queries.add(query.casefold())
                    alternatives.append(query)
            plan.alternative_queries = alternatives
            plan.assumptions = redact(plan.assumptions)
            if not plan.tools or plan.tools != [name for name in TOOL_CHAIN if name in plan.tools]:
                raise ControlledStop('INVALID_PLAN', 'Tools must be unique and run in dependency order.')
            if 'search_documents' in plan.tools and plan.tools != TOOL_CHAIN:
                raise ControlledStop('INVALID_PLAN', 'A new search requires the full retrieval chain.')
            if s['plan'].get('authority') not in (None, plan.authority):
                if plan.tools != TOOL_CHAIN:
                    raise ControlledStop('INVALID_PLAN', 'Changing authorities requires the full retrieval chain.')
                s['evidence'], s['document_status'] = [], []
            signature = json.dumps([plan.authority, plan.query.casefold(), plan.tools])
            if signature in s['seen_actions']:
                raise ControlledStop('REPEATED_ACTION', 'Identical plan produced no progress; stopping.')
            if s['revision'] and plan.tools == TOOL_CHAIN:
                minimum = 5 if plan.authority=='BOTH' else 4
                # Search(es), passage fetch, status, executor and critic.
                if self.limits.max_steps-s['steps'] <= minimum:
                    raise ControlledStop('STEP_CAP', 'Insufficient steps for new retrieval and a verified answer.')
            s['seen_actions'].append(signature)
            s.update(plan=plan.model_dump(), route='executor')
            return s
        except ControlledStop as exc:
            return self.stop(s, exc)

    def retrieve_hits(self, s, plan):
        authority = plan['authority']
        authorities = ('RBI', 'SEBI') if authority == 'BOTH' else (authority,)
        limit = {'UPLOAD':5, 'BOTH':2}.get(authority, 3)
        queries = [plan['query'], *plan['alternative_queries']]
        alternatives_left = 3-len(authorities)
        ids, fetch_query = {}, plan['query']
        for index, regulator in enumerate(authorities):
            for query_index, query in enumerate(queries):
                if query_index:
                    mandatory = len(authorities)-index-1 + 4  # Other searches, fetch, status, draft, critic.
                    if alternatives_left == 0 or self.limits.max_steps-s['steps'] <= mandatory:
                        break
                    alternatives_left -= 1
                hits = self.run_tool(s, 'search_documents', {
                    'query':query, 'authority':regulator, 'fiscal_year':s['fiscal_year'], 'limit':limit,
                })['hits'][:limit]
                if hits:
                    hit_ids = {hit['document_id'] for hit in hits}
                    if len(hit_ids) != len(hits) or hit_ids.intersection(ids):
                        raise ControlledStop('INVALID_TOOL_OUTPUT', 'Search returned ambiguous document IDs.')
                    ids.update(dict.fromkeys((hit['document_id'] for hit in hits), regulator))
                    if authority != 'BOTH':
                        fetch_query = query
                    break
        if not ids:
            scope = 'RBI or SEBI' if authority=='BOTH' else authority
            message = ('No matching passages were found in the conversation attachments.' if authority=='UPLOAD'
                       else f'No matching {scope} documents were found in the retrieval corpus.')
            raise ControlledStop('RETRIEVAL_MISS', message)
        return ids, fetch_query

    def retrieve(self, s):
        plan = s['plan']
        ids = {p['document_id']:p['authority'] for p in s['evidence']}
        previous_snapshots = {(p['document_id'], p['corpus_snapshot']) for p in s['evidence']}
        checked_ids = {d['document_id'] for d in s['document_status']}
        query = plan['query']
        if 'search_documents' in plan['tools']:
            ids, query = self.retrieve_hits(s, plan)
            s['evidence'], s['document_status'] = [], []
        if not ids:
            raise ControlledStop('INVALID_PLAN', 'No retrieved documents are available for this revision.')
        if 'fetch_passages' in plan['tools']:
            evidence = self.run_tool(s, 'fetch_passages', {'document_ids':list(ids), 'query':query})['passages']
            citation_ids = {p['citation_id'] for p in evidence}
            if len(citation_ids) != len(evidence) or any(ids.get(p['document_id']) != p['authority'] for p in evidence):
                raise ControlledStop('INVALID_TOOL_OUTPUT', 'Fetched evidence does not match the selected documents.')
            s['evidence'] = [
                {**passage, **{field:redact(passage[field]) for field in ('text', 'title', 'section', 'source_url')}}
                for passage in evidence
            ]
        snapshots = {(p['document_id'], p['corpus_snapshot']) for p in s['evidence']}
        if ('fetch_passages' in plan['tools'] and 'search_documents' not in plan['tools']
                and snapshots and snapshots <= previous_snapshots
                and ids.keys() <= checked_ids):
            self.event(s, 'control', 'reused_document_status')
            return
        statuses = self.run_tool(s, 'lookup_document_status', {
            'document_ids':list(ids), 'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'],
        })['documents']
        status_ids = {item['document_id'] for item in statuses}
        if len(status_ids) != len(statuses) or not status_ids <= ids.keys():
            raise ControlledStop('INVALID_TOOL_OUTPUT', 'Catalog returned unexpected document statuses.')
        s['document_status'] = redact(catalog_statuses(statuses, s['as_of']))

    def review_context(self, s):
        evidence, _ = label_evidence(s['evidence'], s['document_status'])
        applicable = {item['document_id'] for item in s['document_status'] if item['status']=='applicable'}
        supported = {passage['authority'] for passage in evidence if passage['document_id'] in applicable}
        missing = sorted({'RBI', 'SEBI'} - supported) if s['plan']['authority']=='BOTH' else []
        return {
            'question':self.model_question(s),
            'unavailable_authorities':missing,
            'messages':[] if s['current_attachment_ids'] or s['summary'] else s['messages'],
            'authority':s['plan']['authority'], 'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'],
            'evidence':evidence, 'document_status':s['document_status'], 'uploaded_files':self.upload_catalog(s),
        }

    def executor(self, s):
        try:
            if s['revise_target'] != 'executor':
                self.retrieve(s)
            applicable = {d['document_id'] for d in s['document_status'] if d['status']=='applicable'}
            if not any(p['document_id'] in applicable for p in s['evidence']):
                raise ControlledStop('APPLICABILITY_UNKNOWN', 'No retrieved documents were verified applicable on the requested date.')
            context = self.review_context(s)
            draft = self.model_json(s, 'executor', {
                **context, 'assumptions':s['plan']['assumptions'],
                'feedback':s['feedback'], 'previous_draft':s['draft'] if s['revise_target']=='executor' else {},
            }, Draft)
            if draft.answer_status=='partial' and context['unavailable_authorities']:
                authority = ' or '.join(context['unavailable_authorities'])
                limitation = f'No applicable {authority} documents were available for this comparison.'
                if limitation not in draft.limitations:
                    draft.limitations = [*draft.limitations[:5], limitation]
            private_output = any(contains_private_value(claim.text) for claim in draft.claims)
            s.update(draft=redact(draft.model_dump()), private_output=private_output, route='critic')
            return s
        except ControlledStop as exc:
            return self.stop(s, exc)

    def citation_issues(self, s, labelled):
        evidence = {p['citation_id']:p for p in labelled}
        applicable = {d['document_id'] for d in s['document_status'] if d['status']=='applicable'}
        issues = []
        claims = s['draft']['claims']
        if s['private_output']:
            issues.append('Draft includes a private identifier.')
        if not claims or s['draft']['answer_status']=='not_found':
            issues.append('Draft contains no releasable claims.')
        if s['draft']['answer_status']=='partial' and not any(text.strip() for text in s['draft']['limitations']):
            issues.append('A partial answer must describe the evidence gap.')
        cited_docs, cited_authorities = set(), set()
        for index, claim in enumerate(claims, 1):
            cited_numbers = set()
            if not claim['text'].strip():
                issues.append(f'Claim {index} is empty.')
            for citation in claim['citations']:
                passage = evidence.get(citation)
                if not passage or passage['document_id'] not in applicable:
                    issues.append(f'Claim {index} has an unknown or inapplicable citation.')
                    continue
                cited_docs.add(passage['document_id'])
                cited_authorities.add(passage['authority'])
                cited_numbers.update(numbers(passage['text']))
            if numbers(claim['text']) - cited_numbers:
                issues.append(f'Claim {index} contains a number absent from its cited passages.')
        if any(re.search(r'https?://|\bwww\.', text, re.I) for text in [
            *(claim['text'] for claim in claims), *s['draft']['limitations'],
        ]):
            issues.append('Links must come from verified source metadata.')
        if s['plan']['authority']=='BOTH' and s['draft']['answer_status']=='answered' and cited_authorities != {'RBI','SEBI'}:
            issues.append('A complete comparison must cite both requested regulators.')
        asks_current = re.search(r'\b(newly|just)\s+(attached|uploaded)\b|\b(the|this)\s+(attachment|attached\s+(file|document|image|pdf|csv))\b',
                                 s['question'], re.I)
        if (s['current_attachment_ids'] and asks_current and s['plan']['authority']=='UPLOAD'
                and not cited_docs.intersection(s['current_attachment_ids'])):
            issues.append('The answer must cite a file attached to this message.')
        return issues

    def critic(self, s):
        try:
            context = self.review_context(s)
            issues = self.citation_issues(s, context['evidence'])
            verdict = self.model_json(s, 'critic', {
                **context, 'draft':s['draft'], 'deterministic_issues':issues,
                'revision':s['revision'], 'max_revisions':self.limits.max_revisions,
            }, Critique)
            checks_pass = verdict.checks is not None and all(
                value=='pass' for value in verdict.checks.model_dump().values())
            self.event(s, 'review', 'verdict', decision=verdict.decision,
                       deterministic_issue_count=len(issues), checks_pass=checks_pass)
            if verdict.decision=='accept' and not issues and checks_pass:
                _, citations = label_evidence(s['evidence'], s['document_status'])
                claims = [
                    {'text':claim['text'], 'citations':[citations[tag]['citation_id'] for tag in claim['citations']]}
                    for claim in s['draft']['claims']
                ]
                cited = {citation for claim in claims for citation in claim['citations']}
                sources = [p for p in s['evidence'] if p['citation_id'] in cited]
                s.update(status='answered', route='finish', answer={
                    'claims':claims, 'limitations':redact(s['draft']['limitations']), 'sources':sources,
                })
                return s
            if verdict.decision=='clarify':
                s.update(status='clarify', route='finish', answer={
                    'claims':[], 'limitations':[], 'message':redact(verdict.clarification_question.strip())
                    or 'Which RBI or SEBI rule, date or type of entity should I use?'})
                return s
            if (verdict.decision=='revise' or issues) and s['revision'] < self.limits.max_revisions:
                target = verdict.revise_target or ('executor' if issues else 'planner')
                minimum = 2 if target=='executor' else 4
                if self.limits.max_steps-s['steps'] < minimum:
                    raise ControlledStop('STEP_CAP', 'Insufficient steps for another verified revision.')
                s.update(revision=s['revision']+1, feedback=issues+redact(verdict.issues),
                         revise_target=target, route=target)
                return s
            raise ControlledStop('INSUFFICIENT_EVIDENCE', 'Critic could not verify the answer within the revision limit.')
        except ControlledStop as exc:
            return self.stop(s, exc)

    def finish(self, s):
        # Store complete released answers without copying retrieved passage bodies into memory.
        entry = {'question':s['question'], 'authority':s['plan'].get('authority'),
                 'as_of':s['as_of'], 'fiscal_year':s['fiscal_year'], 'status':s['status'],
                 'answer':json.dumps({k:v for k,v in s['answer'].items() if k != 'sources'}, ensure_ascii=False)}
        if s['status']=='answered' and s['steps'] < self.limits.max_steps:
            authority = entry['authority'] if entry['authority'] != 'UPLOAD' else None
            try:
                summary = self.model_json(s, 'state_summary', {
                    'question':s['question'], 'previous_summary':s['summary'], 'authority':authority,
                    'released_claims':[claim['text'] for claim in s['answer']['claims']],
                    'limitations':s['answer']['limitations'],
                }, StateSummary, attempts=1)
                if authority:
                    summary.authority = authority
                s['summary'] = redact(summary.model_dump())
            except ControlledStop as exc:
                self.event(s, 'control', 'summary_skipped', code=exc.code)
        s['history'] = s['history']+[entry]
        s['messages'] = s['messages']+history_messages([entry])
        self.event(s,'control','finished',status=s['status'], tokens_charged=s['tokens_charged'])
        return s

    def ask(self, thread_id, question, *, as_of=None, fiscal_year=None, messages=None, attachment_passages=None, current_attachment_ids=None, started_at=None, token_budget=None):
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
        if token_budget is not None and (type(token_budget) is not int or token_budget < 1):
            raise ValueError('Token budget must be a positive integer.')
        with self._lock:
            config = {'configurable':{'thread_id':thread_id}, 'recursion_limit':20}
            previous = self.graph.get_state(config).values or {}
            history = redact(previous.get('history', []))
            prior_messages = redact(previous.get('messages', history_messages(history)))
            transcript = redact(messages) if messages is not None else prior_messages
            summary = previous.get('summary', {}) if transcript == prior_messages else {}
            if summary.get('authority') not in (None, 'RBI', 'SEBI', 'BOTH'):
                summary = {}
            uploads = regulatory_uploads(attachment_passages if attachment_passages is not None
                                         else previous.get('attachment_passages', []))
            current_ids = set(current_attachment_ids or ())
            clean_question = redact(question.strip())
            # Follow-ups inherit explicit date/year context, not stale tool evidence or counters.
            resolved_date = as_of or (history[-1]['as_of'] if history else date.today().isoformat())
            resolved_year = fiscal_year or (history[-1].get('fiscal_year') if history else None)
            state = AgentState(
                question=clean_question, resolved_question=clean_question, as_of=resolved_date, fiscal_year=resolved_year,
                private_input=contains_private_value(question), history=history, messages=transcript, summary=summary,
                attachment_passages=redact(uploads),
                current_attachment_ids=list(dict.fromkeys(p['document_id'] for p in uploads if p['document_id'] in current_ids)),
                plan={}, evidence=[], document_status=[], draft={}, private_output=False, feedback=[], revise_target=None,
                answer={}, status='running', revision=0, steps=0, tokens_charged=0, estimated_tokens=0,
                token_budget=min(self.limits.token_budget, token_budget or self.limits.token_budget),
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
