"""Scope restrictions and Indian-number grounding."""
import pytest
from pydantic import ValidationError

from regulatory_agent.agent import RegulatoryAgent
from regulatory_agent.contracts import CatalogSearchArgs, Passage, StateSummary
from regulatory_agent.grounding import regulators_in, regulatory_uploads
from regulatory_agent.models import REFUSAL_MESSAGES, DemoModel
from regulatory_agent.tools import DEMO_DOCUMENTS, CorpusTools


def execute(tmp_path, question, **kwargs):
    agent = RegulatoryAgent(DemoModel(), CorpusTools(DEMO_DOCUMENTS), tmp_path / 'state.sqlite')
    try:
        return agent.ask('tax-test', question, **kwargs)
    finally:
        agent.close()


def test_agent_contract_rejects_tax():
    with pytest.raises(ValidationError):
        CatalogSearchArgs.model_validate({'query': 'monetary limits', 'authority': 'TAX'})
    with pytest.raises(ValidationError):
        Passage.model_validate({
            'citation_id':'legacy:p1', 'document_id':'legacy', 'authority':'TAX',
            'title':'Legacy circular', 'text':'Monetary limits', 'page':1, 'section':'page 1',
            'source_url':'urn:test:legacy', 'corpus_snapshot':'test',
        })
    with pytest.raises(ValidationError):
        StateSummary.model_validate({'authority':'TAX'})


def test_direct_tax_question_is_refused_without_retrieval(tmp_path):
    state = execute(tmp_path, 'What monetary limits does the CBDT circular set for departmental appeals?')
    assert state['status'] == 'refused'
    assert not any(event['kind']=='tool' for event in state['trace'])


@pytest.mark.parametrize('question,category', [
    ('How much income tax will I pay on my FD interest?', 'out_of_scope'),
    ('Should I choose the old or new regime for my salary?', 'personal_advice'),
    ('Can I claim GST input tax credit on office rent?', 'out_of_scope'),
])
def test_tax_refusals(tmp_path, question, category):
    state = execute(tmp_path, question)
    assert state['status'] == 'refused'
    assert state['answer']['message'] == REFUSAL_MESSAGES[category]
    assert not any(e['kind'] == 'tool' for e in state['trace'])


def test_both_still_means_rbi_and_sebi(tmp_path):
    state = execute(tmp_path, 'Compare the RBI complaint and SEBI grievance workflows.')
    assert state['plan']['authority'] == 'BOTH'
    assert {source['authority'] for source in state['answer']['sources']} == {'RBI', 'SEBI'}


def test_tax_uploads_are_out_of_scope():
    assert regulators_in('Central Board of Direct Taxes Circular No. 17/2019') == set()
    assert regulators_in('Income-tax Act, 1961 section 143(2)') == set()
    upload = {'document_id': 'upload-1', 'authority': 'UPLOAD', 'title': 'circular.pdf',
              'text': 'CBDT circular on monetary limits for appeals'}
    assert regulatory_uploads([upload]) == []


@pytest.mark.parametrize('claim,passage', [
    ('The limit before the ITAT is ₹50 lakh.', 'Before Appellate Tribunal 50,00,000'),
    ('Appeals to the Supreme Court need a tax effect above Rs. 2 crore.', 'Before Supreme Court 2,00,00,000'),
    ('The penalty is ₹1.5 lakh.', 'a penalty of Rs. 1,50,000'),
])
def test_lakh_and_crore_amounts_match_their_digit_form(claim, passage):
    from regulatory_agent.grounding import numbers
    assert numbers(claim) - numbers(passage) == set()


def test_unrelated_amounts_still_fail_the_number_check():
    from regulatory_agent.grounding import numbers
    assert numbers('The limit is ₹60 lakh.') - numbers('Before Appellate Tribunal 50,00,000')
