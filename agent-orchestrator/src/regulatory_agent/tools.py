import copy
import re
from datetime import date
from pydantic import ValidationError
from .contracts import (SearchArgs, FetchArgs, StatusArgs, SearchData, FetchData,
                        StatusData, ToolResult, ToolError, call_with_timeout)

# Entirely fictional fixtures: no actual RBI/SEBI rules are asserted.
DEMO_DOCUMENTS = [
    {'document_id':'demo-rbi', 'authority':'RBI', 'title':'SYNTHETIC RBI complaint workflow',
     'text':'In this fictional demo, record the complaint reference and consult the indexed complaint procedure. No regulatory deadline is supplied.',
     'effective_from':'2025-01-01', 'effective_to':None, 'superseded_by':None, 'fiscal_year':None},
    {'document_id':'demo-sebi', 'authority':'SEBI', 'title':'SYNTHETIC SEBI grievance workflow',
     'text':'In this fictional demo, preserve the grievance reference and identify the relevant intermediary before reviewing the procedure.',
     'effective_from':'2025-01-01', 'effective_to':None, 'superseded_by':None, 'fiscal_year':None},
]

class CorpusTools:
    """Replace these methods with the RAG team's hybrid search and catalog APIs.

    Local search is lexical only; it is NOT the team's required hybrid RAG pipeline.
    Returned IDs must be stable and the document catalog must be trusted metadata.
    """
    def __init__(self, documents):
        self.documents = {d['document_id']: copy.deepcopy(d) for d in documents}
        self.failures = {}  # Test-only fault injection, never read from a user prompt.

    def search_documents(self, args: SearchArgs):
        words = set(re.findall(r'\w+', args.query.lower()))
        hits = []
        for d in self.documents.values():
            if d['authority'] != args.authority:
                continue
            if args.fiscal_year and d.get('fiscal_year') not in (None, args.fiscal_year):
                continue
            tokens = set(re.findall(r'\w+', (d['title']+' '+d['text']).lower()))
            overlap = len(words & tokens)
            if overlap:
                hits.append({'document_id':d['document_id'], 'score':float(overlap)})
        return {'hits':sorted(hits, key=lambda h:h['score'], reverse=True)[:args.limit]}

    def fetch_passages(self, args: FetchArgs):
        passages = []
        for document_id in args.document_ids:
            d = self.documents.get(document_id)
            if d is None:
                raise KeyError('Document not found')
            passages.append({
                'citation_id':document_id+':p1', 'document_id':document_id,
                'authority':d['authority'], 'title':d['title'], 'text':d['text'],
                'page':1, 'section':'demo-section',
                'source_url':'urn:synthetic:'+document_id, 'corpus_snapshot':'synthetic-v1'})
        return {'passages':passages}

    def lookup_document_status(self, args: StatusArgs):
        requested = date.fromisoformat(args.as_of)
        results = []
        for document_id in args.document_ids:
            d = self.documents.get(document_id)
            if not d:
                results.append({'document_id':document_id,'status':'unknown','reason':'Not in catalog.'})
                continue
            start, end = d.get('effective_from'), d.get('effective_to')
            if start is None or (d.get('superseded_by') and end is None):
                status, reason = 'unknown', 'Missing effective/supersession metadata.'
            elif requested < date.fromisoformat(start) or (end and requested >= date.fromisoformat(end)):
                status, reason = 'not_applicable', 'Outside catalogued effective interval [from, to).'
            elif args.fiscal_year and d.get('fiscal_year') not in (None, args.fiscal_year):
                status, reason = 'not_applicable', 'Financial year mismatch.'
            else:
                status, reason = 'applicable', 'Matches supplied date/year in this catalog snapshot.'
            results.append({'document_id':document_id,'status':status,'reason':reason})
        return {'documents':results}

TOOL_SCHEMAS = {
    'search_documents': (SearchArgs, SearchData),
    'fetch_passages': (FetchArgs, FetchData),
    'lookup_document_status': (StatusArgs, StatusData),
}


def tool_attempt(tools, name, arguments, timeout):
    """One attempt only. The orchestrator owns retry and step accounting."""
    try:
        input_schema, output_schema = TOOL_SCHEMAS[name]
        args = input_schema.model_validate(arguments)
    except (KeyError, ValidationError):
        return ToolResult(ok=False, error=ToolError(
            code='INVALID_ARGUMENTS', message='Tool name or arguments are invalid.', retryable=False))
    try:
        def invoke():
            faults = tools.failures.get(name, [])
            if faults:
                raise faults.pop(0)
            return getattr(tools, name)(args)
        raw = call_with_timeout(invoke, timeout)
        data = output_schema.model_validate(raw).model_dump()
        return ToolResult(ok=True, data=data)
    except (TimeoutError, ConnectionError):
        return ToolResult(ok=False, error=ToolError(
            code='TIMEOUT', message='Tool unavailable within the deadline.', retryable=True))
    except ValidationError:
        return ToolResult(ok=False, error=ToolError(
            code='INVALID_TOOL_OUTPUT', message='Tool returned an invalid response.', retryable=False))
    except KeyError:
        return ToolResult(ok=False, error=ToolError(
            code='NOT_FOUND', message='Requested document was not found.', retryable=False))
    except Exception:
        return ToolResult(ok=False, error=ToolError(
            code='TOOL_FAILURE', message='Tool failed; internal details suppressed.', retryable=False))


class HttpCorpusTools:
    """Read-only retrieval API adapter; tool_attempt retains schema validation/retries."""
    def __init__(self, base_url, api_key='', timeout=15, client=None):
        import httpx
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Use an HTTP(S) retrieval service URL without embedded credentials.')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost','127.0.0.1','::1','retrieval','host.docker.internal'):
            raise ValueError('Use HTTPS outside localhost or the retrieval Docker service.')
        self.base_url = base_url.rstrip('/')
        self.api_key, self.timeout = api_key, timeout
        self.client = client or httpx.Client(timeout=timeout)
        self.owns_client = client is None
        self.failures = {}

    def _call(self, name, args):
        import httpx
        try:
            response = self.client.post(self.base_url+'/v1/tools/'+name,
                                        json=args.model_dump(), timeout=self.timeout,
                                        headers={'X-API-Key': self.api_key} if self.api_key else {})
        except httpx.TimeoutException as exc:
            raise TimeoutError('Retrieval request timed out.') from exc
        except httpx.TransportError as exc:
            raise ConnectionError('Retrieval service unavailable.') from exc
        if response.status_code == 404:
            raise KeyError('Document not found')
        if response.status_code in (408, 429) or response.status_code >= 500:
            raise ConnectionError('Retrieval service temporarily unavailable.')
        if response.is_error:
            raise RuntimeError('Retrieval service rejected the request.')
        return response.json()

    def search_documents(self, args):
        return self._call('search_documents', args)

    def fetch_passages(self, args):
        return self._call('fetch_passages', args)

    def lookup_document_status(self, args):
        return self._call('lookup_document_status', args)

    def close(self):
        if self.owns_client:
            self.client.close()
