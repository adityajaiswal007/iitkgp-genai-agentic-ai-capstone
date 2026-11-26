"""Bounded file extraction worker. Invoked in a separate, resource-limited process."""
import base64
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAX_TEXT = 300_000
MAX_ARCHIVE = 20 * 1024 * 1024


def ocr(image):
    if image.width * image.height > 12_000_000:
        raise ValueError('Image exceeds the OCR pixel limit.')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/'image.png'
        image.convert('RGB').save(path)
        result = subprocess.run(['tesseract', str(path), 'stdout', '--psm', '6'],
                                capture_output=True, timeout=8, check=True)
        return result.stdout.decode('utf-8').strip()


def extract(data, mime):
    pages = []
    if mime.startswith('text/') or mime == 'application/json':
        text = data.decode('utf-8-sig')
        if mime == 'application/json':
            json.loads(text)
        pages = [(1, 'Text', text)]
    elif mime == 'application/pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError('PDF must be unencrypted and at most 100 pages.')
        rendered = None
        try:
            for index, page in enumerate(reader.pages):
                stream = page.get_contents()
                if stream is not None and len(stream.get_data()) > 8 * 1024 * 1024:
                    raise ValueError('PDF content stream is too large.')
                text = page.extract_text(extraction_mode='layout') or ''
                section = 'PDF text'
                if not text.strip():
                    import pypdfium2
                    if rendered is None:
                        rendered = pypdfium2.PdfDocument(data)
                    pdf_page = rendered[index]
                    if pdf_page.get_width()*pdf_page.get_height()*4 > 12_000_000:
                        raise ValueError('PDF page exceeds the OCR pixel limit.')
                    bitmap = pdf_page.render(scale=2)
                    try:
                        text = ocr(bitmap.to_pil())
                    finally:
                        bitmap.close()
                        pdf_page.close()
                    section = 'Scanned PDF OCR'
                if text.strip():
                    pages.append((index+1, section, text))
                if sum(len(p[2]) for p in pages) > MAX_TEXT:
                    raise ValueError('Extracted text exceeds the document limit.')
        finally:
            if rendered is not None:
                rendered.close()
    elif mime.startswith('image/'):
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 12_000_000
        with Image.open(io.BytesIO(data)) as image:
            expected = {'image/png':'PNG','image/jpeg':'JPEG','image/webp':'WEBP','image/gif':'GIF'}[mime]
            if image.format != expected:
                raise ValueError('Image format mismatch.')
            pages = [(1, 'Image OCR (first frame)', ocr(image))]
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > MAX_ARCHIVE:
                raise ValueError('Office archive is too large when expanded.')
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError('Encrypted Office files are unsupported.')
            if mime.endswith('wordprocessingml.document'):
                from defusedxml import ElementTree
                root = ElementTree.fromstring(archive.read('word/document.xml'))
                ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                paragraphs = [''.join(paragraph.itertext()) for paragraph in root.iter(ns+'p')]
                # itertext includes only XML text values, never executes document fields/macros.
                pages = [(1, 'DOCX paragraphs and tables', '\n'.join(paragraphs))]
            elif mime.endswith('spreadsheetml.sheet'):
                from openpyxl import load_workbook
                workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
                try:
                    total = 0
                    for index, sheet in enumerate(workbook.worksheets):
                        rows = []
                        for row in sheet.iter_rows(values_only=True):
                            line = '\t'.join('' if value is None else str(value) for value in row)
                            total += len(line)+1
                            if total > MAX_TEXT:
                                raise ValueError('Extracted spreadsheet is too large.')
                            rows.append(line)
                        pages.append((index+1, 'Sheet: '+sheet.title+' (cached values; formulas not evaluated)', '\n'.join(rows)))
                finally:
                    workbook.close()
            else:
                raise ValueError('Unsupported document type.')
    if sum(len(p[2]) for p in pages) > MAX_TEXT or not any(p[2].strip() for p in pages):
        raise ValueError('No readable text, or extracted text is too large.')
    return [{'page':page, 'section':section, 'text':text} for page,section,text in pages if text.strip()]


if __name__ == '__main__':
    # Linux limits include child OCR processes. The parent independently enforces wall time.
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    try:
        payload = json.load(sys.stdin)
        pages = extract(base64.b64decode(payload['content_base64'], validate=True), payload['mime_type'])
        print(json.dumps({'pages':pages}, ensure_ascii=False))
    except Exception:
        print(json.dumps({'error':'The file could not be read within the extraction limits. Use a readable, unencrypted document or a clear text image.'}))
