"""Conversation-scoped uploaded evidence; file contents are never executable instructions."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_FILE_BYTES = 5*1024*1024
MAX_TOTAL_BYTES = 25*1024*1024
MAX_UPLOAD_BODY = 36*1024*1024
MIME_TYPES = {
    '.txt':'text/plain', '.md':'text/markdown', '.csv':'text/csv', '.json':'application/json',
    '.pdf':'application/pdf', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp', '.gif':'image/gif',
}


class AttachmentError(ValueError):
    pass


class UploadedFile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=180)
    mime_type: str
    current: bool = True
    content_base64: str = Field(min_length=1, max_length=7*1024*1024)

    @field_validator('name')
    @classmethod
    def safe_name(cls, value):
        if re.search(r'[\\/\x00-\x1f\x7f]', value):
            raise ValueError('Invalid attachment name.')
        return value



def _extractor_env():
    """Minimal environment for the extraction subprocess (no API keys or secrets).

    UTF-8 is forced for its input and output, because Windows otherwise uses a legacy
    code page that cannot represent ₹ or Devanagari text. Windows also needs SYSTEMROOT
    (and a temp folder for OCR) for a Python subprocess to start.
    """
    env = {'PATH': os.environ.get('PATH', os.defpath), 'LANG': 'C.UTF-8', 'OMP_THREAD_LIMIT': '1',
           'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    if os.name == 'nt':
        for name in ('SYSTEMROOT', 'SystemRoot', 'TEMP', 'TMP', 'PATHEXT', 'COMSPEC'):
            if os.environ.get(name):
                env[name] = os.environ[name]
    return env

def extract_attachments(files):
    decoded, total = [], 0
    for file in files:
        if MIME_TYPES.get(PurePath(file.name).suffix.lower()) != file.mime_type:
            raise AttachmentError('Attachment type does not match its file extension.')
        try:
            data = base64.b64decode(file.content_base64, validate=True)
        except ValueError:
            raise AttachmentError('Invalid attachment encoding.') from None
        total += len(data)
        if not data or len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise AttachmentError('Files must be nonempty, at most 5 MB each and 25 MB per conversation request.')
        decoded.append((file, data))
    passages, seen = [], set()
    total_text = 0
    deadline = time.monotonic()+30
    for file, data in decoded:
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        try:
            timeout = min(15, deadline-time.monotonic())
            if timeout <= 0:
                raise TimeoutError()
            result = subprocess.run([sys.executable, '-m', 'regulatory_agent.attachment_extract'],
                input=json.dumps({'content_base64':file.content_base64, 'mime_type':file.mime_type}),
                text=True, encoding='utf-8', capture_output=True, timeout=timeout, check=True,
                env=_extractor_env())
            pages = json.loads(result.stdout)
            if 'error' in pages:
                raise AttachmentError(file.name+': '+pages['error'])
        except AttachmentError:
            raise
        except (subprocess.SubprocessError, ValueError, TimeoutError):
            raise AttachmentError(file.name+': Extraction failed or exceeded its time/memory limit.') from None
        total_text += sum(len(page['text']) for page in pages['pages'])
        if total_text > 500_000:
            raise AttachmentError('Total extracted attachment text exceeds 500,000 characters. Split the files across chats.')
        for page in pages['pages']:
            text = page['text'].strip()
            for offset in range(0, len(text), 2800):
                chunk = text[offset:offset+3000]
                passages.append({
                    'citation_id':f'upload:{digest}:p{page["page"]}:c{offset}',
                    'document_id':'upload-'+digest, 'authority':'UPLOAD', 'title':file.name,
                    'text':chunk, 'page':page['page'], 'section':page['section'],
                    'source_url':'urn:attachment:'+digest, 'corpus_snapshot':digest,
                })
    return passages


class AttachmentTools:
    def __init__(self, base, passages, current_ids=()):
        self.base, self.passages = base, passages
        self.failures = {}
        self.current_ids = set(current_ids)
        self.ids = {p['document_id'] for p in passages}

    @staticmethod
    def score(query, passage):
        words = set(re.findall(r'\w+', (query or '').casefold()))
        tokens = set(re.findall(r'\w+', (passage['title']+' '+passage['text']).casefold()))
        return len(words & tokens)

    def search_documents(self, args):
        if args.authority != 'UPLOAD':
            return self.base.search_documents(args)
        scores = {}
        for passage in self.passages:
            did = passage['document_id']
            scores[did] = max(scores.get(did, 0), self.score(args.query, passage))
        return {'hits':[{'document_id':did,'score':float(score)}
                        for did,score in sorted(scores.items(),key=lambda item:(item[1], item[0] in self.current_ids),reverse=True)[:args.limit]]}

    def fetch_passages(self, args):
        if not any(did in self.ids for did in args.document_ids):
            return self.base.fetch_passages(args)
        if any(did not in self.ids for did in args.document_ids):
            raise KeyError('Mixed or unknown attachment IDs.')
        selected = []
        for did in args.document_ids:
            candidates = [p for p in self.passages if p['document_id']==did]
            selected.extend(sorted(candidates,key=lambda p:self.score(args.query,p),reverse=True)[:2])
        return {'passages':selected}

    def lookup_document_status(self, args):
        if not any(did in self.ids for did in args.document_ids):
            return self.base.lookup_document_status(args)
        return {'documents':[{'document_id':did,
            'status':'applicable' if did in self.ids else 'unknown',
            'reason':'User-provided file evidence available in this conversation.'}
            for did in args.document_ids]}
