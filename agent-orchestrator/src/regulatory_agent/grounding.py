"""Evidence boundaries and deterministic checks shared by the prompt workflow."""

import re
from datetime import date
from decimal import Decimal

_REGULATOR_NAMES = {
    'RBI': re.compile(r'\b(?:RBI|Reserve Bank of India)\b', re.I),
    'SEBI': re.compile(r'\b(?:SEBI|Securities and Exchange Board of India)\b', re.I),
}
_PRIVATE_VALUE = re.compile(
    r'\b[A-Z]{5}\d{4}[A-Z]\b|\b[\w.+-]+@[\w.-]+\.[A-Z]{2,}\b|'
    r'\b(?:aadhaar|account|card|mobile|phone)(?:\s+(?:number|no\.?))?\s*(?:is\s+|[:#]\s*)?'
    r'\d(?:[ -]?\d){7,18}\b',
    re.I,
)
_DELIMITERS = re.compile(r'</?\s*(?:documents?|tool_result|user_message|think)\b[^>]*>', re.I)
_DIGITS = re.compile(r'(?<![\w.])[+-]?\d+(?:[,\u202f]\d{2,3})*(?:\.\d+)?(?!\w|\.\d)')
_SMALL = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
_TENS = 'twenty thirty forty fifty sixty seventy eighty ninety'.split()
_WORD_VALUES = {**dict(zip(_SMALL, range(20))), **dict(zip(_TENS, range(20, 100, 10)))}
_WORD_NUMBER = re.compile(
    r'\b(?:' + '|'.join(_TENS) + r')(?:[ -](?:' + '|'.join(_SMALL[1:10]) + r'))?\b'
    r'|\b(?:' + '|'.join(_SMALL) + r')\b', re.I,
)


def regulators_in(text):
    return {regulator for regulator, pattern in _REGULATOR_NAMES.items() if pattern.search(text)}


def redact(value):
    if isinstance(value, str):
        return _PRIVATE_VALUE.sub('[redacted]', value)
    if isinstance(value, list):
        return [redact(part) for part in value]
    if isinstance(value, dict):
        return {key: redact(part) for key, part in value.items()}
    return value


def contains_private_value(text):
    return bool(_PRIVATE_VALUE.search(text))


def prompt_data(value):
    if isinstance(value, str):
        return _DELIMITERS.sub(lambda match: match[0].replace('<', '‹').replace('>', '›'), redact(value))
    if isinstance(value, list):
        return [prompt_data(part) for part in value]
    if isinstance(value, dict):
        return {key: prompt_data(part) for key, part in value.items()}
    return value


def regulatory_uploads(passages):
    regulatory_ids = {
        p['document_id'] for p in passages if regulators_in(p['title'] + ' ' + p['text'])
    }
    return [p for p in passages if p['document_id'] in regulatory_ids and p['authority'] == 'UPLOAD']


def label_evidence(passages, statuses):
    applicable = {item['document_id'] for item in statuses if item['status']=='applicable'}
    ranked = sorted(passages, key=lambda passage: passage['document_id'] not in applicable)
    selected = ranked[:8]
    represented = {passage['authority'] for passage in selected}
    for passage in ranked[8:]:
        if passage['authority'] not in represented and passage['document_id'] in applicable:
            selected[-1] = passage
            break
    labelled = []
    citations = {}
    for number, passage in enumerate(selected, 1):
        tag = f'S{number}'
        labelled.append(prompt_data({
            **passage, 'citation_id': tag, 'text': passage['text'][:1800],
        }))
        citations[tag] = passage
    return labelled, citations


def catalog_statuses(statuses, as_of):
    """Apply explicit catalog dates and replacements for the requested date."""
    requested = date.fromisoformat(as_of)
    checked = []
    for record in statuses:
        status = record.copy()
        if status['status']=='applicable':
            try:
                start = date.fromisoformat(status['effective_from']) if status.get('effective_from') else None
                end = date.fromisoformat(status['effective_to']) if status.get('effective_to') else None
            except ValueError:
                status.update(status='unknown', reason='Catalog has invalid effective dates.')
            else:
                if (start and requested < start) or (end and requested >= end):
                    status.update(status='not_applicable', reason='Outside the catalogued effective interval.')
        checked.append(status)
    applicable = {status['document_id'] for status in checked if status['status']=='applicable'}
    for status in checked:
        replacement = status.get('superseded_by')
        if status['status']=='applicable' and replacement in applicable:
            status.update(status='not_applicable', reason='Catalog identifies an applicable replacement.')
    return checked


_INDIAN_UNITS = re.compile(
    r'(?<![\w.])(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr\.?)(?![a-z])', re.I)
_UNIT_VALUES = {'lakh': Decimal(100000), 'lac': Decimal(100000), 'crore': Decimal(10000000), 'cr': Decimal(10000000)}


def _expand_indian_units(text):
    """'50 lakh' -> '5000000' and '2 crore' -> '20000000', so amounts match however they are written."""
    def value(match):
        unit = match.group(2).lower().rstrip('.').rstrip('s')
        amount = Decimal(match.group(1)) * _UNIT_VALUES['lac' if unit == 'la' else unit]
        return format(amount.normalize(), 'f')
    return _INDIAN_UNITS.sub(value, text)


def numbers(text):
    text = _expand_indian_units(text)
    digits = {
        Decimal(raw.replace(',', '').replace('\u202f', ''))
        for raw in _DIGITS.findall(text)
    }
    words = {
        Decimal(sum(_WORD_VALUES[word] for word in match.lower().replace('-', ' ').split()))
        for match in _WORD_NUMBER.findall(text)
    }
    return digits | words
