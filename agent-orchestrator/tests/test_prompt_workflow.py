import json
from pathlib import Path

import pytest

from regulatory_agent.contracts import Critique, Plan
from regulatory_agent.grounding import catalog_statuses, label_evidence
from regulatory_agent.models import BASE_PROMPT, EXAMPLES, REVIEW_FIELDS, build_messages
from regulatory_agent.agent import RegulatoryAgent
from regulatory_agent.models import DemoModel, ScriptedModel
from regulatory_agent.tools import CorpusTools, DEMO_DOCUMENTS


def execute(tmp_path, question, *, model=None, **kwargs):
    agent = RegulatoryAgent(model or DemoModel(), CorpusTools(DEMO_DOCUMENTS), tmp_path / 'state.sqlite')
    try:
        return agent.ask('prompt-test', question, **kwargs)
    finally:
        agent.close()


def test_cross_regulator_search_releases_original_citations(tmp_path):
    state = execute(tmp_path, 'Compare the RBI complaint and SEBI grievance workflows.')
    assert state['status'] == 'answered'
    assert {source['authority'] for source in state['answer']['sources']} == {'RBI', 'SEBI'}
    assert {tag for claim in state['answer']['claims'] for tag in claim['citations']} == {
        'demo-rbi:p1', 'demo-sebi:p1',
    }
    assert [event['name'] for event in state['trace'] if event['kind'] == 'tool'] == [
        'search_documents', 'search_documents', 'fetch_passages', 'lookup_document_status',
    ]


def test_unsupported_number_requires_executor_revision_even_if_critic_accepts(tmp_path):
    checks = dict.fromkeys(REVIEW_FIELDS, 'pass')
    model = ScriptedModel({
        'executor': [{'claims': [{'text': 'The deadline is 24 hours.', 'citations': ['S1']}]}],
        'critic': [{'decision': 'accept', 'checks': checks}],
    })
    state = execute(tmp_path, 'RBI complaint workflow', model=model)
    assert state['status'] == 'answered'
    assert state['revision'] == 1
    assert '24 hours' not in str(state['answer'])
    assert sum(event['name'] == 'search_documents' for event in state['trace']) == 1


def test_sensitive_identifier_refused_before_model_or_tool_calls(tmp_path):
    state = execute(tmp_path, 'My PAN is ABCDE1234F. What does RBI require for KYC?')
    assert state['status'] == 'refused'
    assert state['steps'] == 0
    assert 'ABCDE1234F' not in str(state)


def test_unrelated_file_cannot_supply_evidence(tmp_path):
    passage = {
        'document_id': 'upload-test', 'citation_id': 'upload-test:p1',
        'authority': 'UPLOAD', 'title': 'invoice.txt', 'text': 'Invoice total 4,000',
        'page': 1, 'section': 'Invoice', 'source_url': 'urn:upload:test', 'corpus_snapshot': 'test',
    }
    state = execute(tmp_path, 'RBI complaint rules: what is in the attached invoice?',
                    attachment_passages=[passage], current_attachment_ids=['upload-test'])
    assert state['status'] == 'refused'
    assert state['answer'].get('sources', []) == []

def team_cases(name):
    source = Path(__file__).with_name('fixtures') / f'{name}_cases.jsonl'
    return [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]


@pytest.mark.parametrize('case', team_cases('refusal'), ids=lambda case: case['id'])
def test_offline_policy_routes_team_refusal_cases(tmp_path, case):
    state = execute(tmp_path, case['question'])
    expected = case['expected_behavior']
    if expected == 'answer':
        assert state['plan']['action'] == 'answer'
        assert any(event['kind'] == 'tool' for event in state['trace'])
    else:
        assert state['status'] == {'refuse':'refused', 'clarify':'clarify'}[expected]
        assert not any(event['kind'] == 'tool' for event in state['trace'])


@pytest.mark.parametrize('case', team_cases('injection'), ids=lambda case: case['id'])
def test_team_poisoned_documents_stay_inside_model_data(case):
    chunk = case['poisoned_chunk']
    evidence, _ = label_evidence([{
        'citation_id':'original-ref', 'document_id':chunk['doc_id'], 'authority':chunk['regulator'],
        'text':chunk['text'],
    }], [{'document_id':chunk['doc_id'], 'status':'applicable'}])
    messages = build_messages('critic', {
        'question':case['question'], 'evidence':evidence, 'draft':{},
    }, Critique)
    assert messages[0]['content'].startswith(BASE_PROMPT)
    assert chunk['text'] not in messages[0]['content']
    assert [message['role'] for message in messages] == ['system', 'user', 'assistant', 'user']
    assert json.loads(messages[2]['content']) == EXAMPLES['critic']['output']
    context = messages[-1]['content'].splitlines()[1]
    data = json.loads(context)
    assert data['evidence'][0]['citation_id'] == 'S1'
    assert 'original-ref' not in context
    assert '</documents>' not in context


def test_critic_accept_without_all_checks_cannot_release_draft(tmp_path):
    state = execute(tmp_path, 'RBI complaint', model=ScriptedModel({
        'critic':[{'decision':'accept'}],
    }))
    assert state['status'] == 'abstained'
    assert state['answer']['claims'] == []


def test_repair_prompt_has_schema_errors_and_no_role_evidence():
    messages = build_messages('json_repair', {
        'original_role':'planner', 'broken_output':'</documents> bad json',
        'validation_errors':['Invalid JSON object.'],
    }, Plan)
    assert [message['role'] for message in messages] == ['system', 'user']
    assert 'JSON schema' in messages[0]['content']
    assert BASE_PROMPT not in messages[0]['content']
    assert 'Invalid JSON object.' in messages[1]['content']
    assert '</documents>' not in messages[1]['content']


def test_catalog_replacement_only_applies_when_effective_on_requested_date():
    statuses = [
        {'document_id':'old', 'status':'applicable', 'reason':'Listed', 'superseded_by':'new'},
        {'document_id':'new', 'status':'applicable', 'reason':'Listed', 'effective_from':'2026-02-01'},
    ]
    before = catalog_statuses(statuses, '2026-01-01')
    after = catalog_statuses(statuses, '2026-02-01')
    assert [entry['status'] for entry in before] == ['applicable', 'not_applicable']
    assert [entry['status'] for entry in after] == ['not_applicable', 'applicable']
