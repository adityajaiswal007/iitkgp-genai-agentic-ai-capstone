"""Income-tax (TAX) authority: contract, routing, refusals and uploads."""
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


def test_contract_accepts_tax_and_still_rejects_unknown_authorities():
    CatalogSearchArgs.model_validate({'query': 'monetary limits', 'authority': 'TAX'})
    Passage.model_validate({
        'citation_id': 'tax-circular-3-2018:p1:c0', 'document_id': 'tax-circular-3-2018', 'authority': 'TAX',
        'title': 'CBDT Circular No. 3/2018', 'text': 'Monetary limits ...', 'page': 1, 'section': 'page 1',
        'source_url': 'urn:regassist:tax:tax-circular-3-2018#page=1', 'corpus_snapshot': 'test'})
    StateSummary.model_validate({'authority': 'TAX'})
    with pytest.raises(ValidationError):
        CatalogSearchArgs.model_validate({'query': 'x', 'authority': 'IRDAI'})


def test_income_tax_question_is_answered_from_tax_evidence(tmp_path):
    state = execute(tmp_path, 'What monetary limits does the CBDT circular set for departmental appeals?')
    assert state['plan']['authority'] == 'TAX'
    assert state['status'] == 'answered'
    assert {source['authority'] for source in state['answer']['sources']} == {'TAX'}
    assert [e['name'] for e in state['trace'] if e['kind'] == 'tool'] == [
        'search_documents', 'fetch_passages', 'lookup_document_status']


@pytest.mark.parametrize('question,category', [
    ('How much income tax will I pay on my FD interest?', 'personal_advice'),
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


def test_tax_uploads_count_as_regulatory_evidence():
    assert regulators_in('Central Board of Direct Taxes Circular No. 17/2019') == {'TAX'}
    assert regulators_in('Income-tax Act, 1961 section 143(2)') == {'TAX'}
    upload = {'document_id': 'upload-1', 'authority': 'UPLOAD', 'title': 'circular.pdf',
              'text': 'CBDT circular on monetary limits for appeals'}
    assert regulatory_uploads([upload]) == [upload]


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
