import json
import time
from pathlib import Path
from regulatory_agent.agent import RegulatoryAgent, export_trace
from regulatory_agent.contracts import Limits, parse_json, Critique, call_with_timeout
from regulatory_agent.tools import CorpusTools, DEMO_DOCUMENTS
from regulatory_agent.models import DemoModel, ScriptedModel

import tempfile
import unittest

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / 'state.sqlite')
        self.agents = []

    def tearDown(self):
        for a in self.agents:
            a.close()
        self.tmp.cleanup()

    def agent(self, model=None, tools=None, **limits):
        a = RegulatoryAgent(model or DemoModel(), tools or CorpusTools(DEMO_DOCUMENTS),
                            self.db, Limits(**limits))
        self.agents.append(a)
        return a

    def test_three_tools_and_cited_answer(self):
        s = self.agent().ask('t1', 'What is the RBI demo complaint process?')
        self.assertEqual(s['status'], 'answered')
        self.assertEqual([e['name'] for e in s['trace'] if e['kind']=='tool'],
                         ['search_documents', 'fetch_passages', 'lookup_document_status'])
        self.assertTrue(s['answer']['claims'][0]['citations'])
        self.assertLessEqual(s['steps'], 10)

    def test_persistence_restart_and_isolation(self):
        a = self.agent()
        a.ask('t1', 'Explain the RBI demo complaint process')
        a.close()
        b = self.agent()
        s = b.ask('t1', 'What about the follow-up?')
        self.assertEqual(s['plan']['authority'], 'RBI')
        self.assertEqual(len(s['history']), 2)
        self.assertEqual(s['steps'], 6)
        isolated = b.ask('t2', 'What about the follow-up?')
        self.assertEqual(isolated['status'], 'clarify')
        self.assertEqual(len(isolated['history']), 1)

    def test_malformed_json_repaired_once(self):
        s = self.agent(ScriptedModel({'planner': ['{bad']})).ask('t', 'RBI demo complaint')
        self.assertEqual(s['status'], 'answered')
        self.assertEqual(s['steps'], 7)
        self.assertTrue(any(e.get('code')=='INVALID_MODEL_OUTPUT' for e in s['trace']))

    def test_persistent_malformed_json_stops(self):
        s = self.agent(ScriptedModel({'planner': ['oops', 'still bad']})).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 2)

    def test_transient_tool_failure_retry(self):
        tools = CorpusTools(DEMO_DOCUMENTS)
        tools.failures['search_documents'] = [TimeoutError()]
        s = self.agent(tools=tools).ask('t', 'RBI demo complaint')
        self.assertEqual(s['status'], 'answered')
        self.assertEqual(s['steps'], 7)
        self.assertTrue(any(e.get('code')=='TIMEOUT' for e in s['trace']))

    def test_permanent_tool_failure_stops(self):
        tools = CorpusTools(DEMO_DOCUMENTS)
        tools.failures['search_documents'] = [ValueError('private detail')]
        s = self.agent(tools=tools).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertNotIn('private detail', json.dumps(s))

    def test_bad_tool_payload_rejected(self):
        class BadTools(CorpusTools):
            def search_documents(self, args):
                return {'unexpected': True}
        s = self.agent(tools=BadTools(DEMO_DOCUMENTS)).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertTrue(any(e.get('code')=='INVALID_TOOL_OUTPUT' for e in s['trace']))

    def test_token_cap_prevents_call(self):
        s = self.agent(token_budget=1).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 0)
        self.assertEqual(s['tokens_charged'], 0)

    def test_step_cap_and_revision(self):
        model = ScriptedModel({'critic': [{'decision':'revise', 'issues':['Find SEBI evidence too.']}],
                               'planner': [None, {'action':'answer', 'query':'Read more context for RBI complaint',
                                                   'authority':'RBI', 'tools':['fetch_passages']}]})
        s = self.agent(model).ask('t', 'RBI demo complaint')
        self.assertEqual(s['status'], 'answered')
        self.assertEqual(s['steps'], 10)
        self.assertEqual(s['revision'], 1)

    def test_repeated_plan_cannot_loop(self):
        model = ScriptedModel({'critic': [{'decision':'revise', 'issues':['Search again.']}]})
        s = self.agent(model).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertTrue(any(e.get('code')=='REPEATED_ACTION' for e in s['trace']))

    def test_unknown_citation_never_released(self):
        draft = {'claims':[{'text':'Invented claim', 'citations':['made-up']}], 'limitations':[]}
        s = self.agent(ScriptedModel({'executor':[draft, draft]})).ask('t', 'RBI complaint')
        self.assertNotEqual(s['status'], 'answered')
        self.assertEqual(s['answer']['claims'], [])

    def test_unknown_applicability_never_released(self):
        docs = [dict(d, effective_from=None) for d in DEMO_DOCUMENTS]
        s = self.agent(tools=CorpusTools(docs)).ask('t', 'RBI complaint', as_of='2026-01-01')
        self.assertEqual(s['status'], 'abstained')

    def test_timeout_returns_promptly(self):
        def slow():
            time.sleep(0.15)
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            call_with_timeout(slow, 0.01)
        self.assertLess(time.monotonic()-start, 0.12)

    def test_model_transport_error_is_handled(self):
        class Down(DemoModel):
            def complete(self, *args, **kwargs):
                raise TimeoutError()
        s = self.agent(Down()).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 2)

    def test_fiscal_year_mismatch(self):
        s = self.agent().ask('t', 'Income Tax demo evidence', fiscal_year='2099-00')
        self.assertEqual(s['status'], 'abstained')

    def test_unknown_tool_plan_is_rejected(self):
        bad = {'action':'answer','query':'RBI','authority':'RBI','tools':['run_shell']}
        s = self.agent(ScriptedModel({'planner':[bad, bad]})).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertFalse(any(e['kind']=='tool' for e in s['trace']))

    def test_json_reasoning_and_code_fence(self):
        raw = '<think>internal</think>\n```json\n{"decision":"accept","issues":[]}\n```'
        self.assertEqual(parse_json(raw, Critique).decision, 'accept')

    def test_critic_rejection_does_not_expose_draft(self):
        s = self.agent(ScriptedModel({'critic':[{'decision':'insufficient_evidence', 'issues':['Unsupported.']}]})).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['answer']['claims'], [])

    def test_small_step_cap_stops_before_next_call(self):
        s = self.agent(max_steps=3).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 3)
        self.assertEqual(s['answer']['claims'], [])

    def test_tool_retries_are_bounded(self):
        tools = CorpusTools(DEMO_DOCUMENTS)
        tools.failures['search_documents'] = [TimeoutError(), TimeoutError(), TimeoutError()]
        s = self.agent(tools=tools).ask('t', 'RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 3)
        self.assertEqual(len(tools.failures['search_documents']), 1)

    def test_no_revision_when_disabled(self):
        model = ScriptedModel({'critic':[{'decision':'revise','issues':['Need context.']}]})
        s = self.agent(model, max_revisions=0).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 6)

    def test_draft_json_repair_is_accounted(self):
        s = self.agent(ScriptedModel({'executor':['bad JSON']})).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'answered')
        self.assertEqual(s['steps'], 7)

    def test_missing_usage_charges_reservation(self):
        class NoUsage(DemoModel):
            def complete(self, *args, **kwargs):
                raw, _ = super().complete(*args, **kwargs)
                return raw, None
        s = self.agent(NoUsage()).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'answered')
        self.assertGreater(s['tokens_charged'], 300)
        self.assertLessEqual(s['tokens_charged'], 48000)

    def test_provider_usage_overrun_stops(self):
        class Overrun(DemoModel):
            def complete(self, *args, **kwargs):
                raw, _ = super().complete(*args, **kwargs)
                return raw, 999999
        s = self.agent(Overrun()).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertTrue(any(e.get('code')=='PROVIDER_USAGE_OVERRUN' for e in s['trace']))

    def test_tax_year_survives_followup(self):
        a = self.agent()
        a.ask('t','Income Tax demo evidence', fiscal_year='2025-26')
        s = a.ask('t','What about the follow-up?')
        self.assertEqual(s['fiscal_year'], '2025-26')
        self.assertEqual(s['plan']['authority'], 'INCOME_TAX')

    def test_input_validation(self):
        a = self.agent()
        for thread, question in [('bad/id','RBI'), ('t',''), ('t','x'*4001)]:
            with self.assertRaises(ValueError):
                a.ask(thread, question)
        with self.assertRaises(ValueError):
            a.ask('t','RBI', as_of='2025-02-30')

    def test_second_critic_rejection_stops(self):
        model = ScriptedModel({
            'planner':[None, {'action':'answer','authority':'RBI','query':'Read full complaint context',
                              'tools':['fetch_passages']}],
            'critic':[{'decision':'revise','issues':['Read full context.']},
                      {'decision':'revise','issues':['Still insufficient.']}]})
        s = self.agent(model).ask('t','RBI complaint')
        self.assertEqual(s['status'], 'abstained')
        self.assertEqual(s['steps'], 10)
        self.assertEqual(s['answer']['claims'], [])

    def test_trace_export_omits_question_and_passages(self):
        s = self.agent().ask('t','RBI complaint secret-marker')
        path = Path(self.tmp.name)/'trace.jsonl'
        export_trace(s,path)
        exported = path.read_text()
        self.assertNotIn('secret-marker',exported)
        self.assertNotIn('fictional demo',exported)
        self.assertTrue(all(json.loads(line) for line in exported.splitlines()))
