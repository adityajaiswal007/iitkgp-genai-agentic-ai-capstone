import io
import shutil
import zipfile

import pytest
from PIL import Image, ImageDraw, ImageFont
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from regulatory_agent.attachment_extract import extract, MAX_ARCHIVE
from regulatory_agent.attachments import UploadedFile, extract_attachments, AttachmentError
from test_upload_api import upload


def pdf_bytes():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 16 Tf 40 700 Td (Invoice total 7643) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_keeps_real_page_numbers_and_text():
    pages = extract(pdf_bytes(), 'application/pdf')
    assert pages[0]['page'] == 1
    assert '7643' in pages[0]['text']


def test_docx_reads_paragraphs_and_table_cells_without_executing_fields():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Contract total 5721</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Signed: yes</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    pages = extract(stream.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert '5721' in pages[0]['text'] and 'Signed: yes' in pages[0]['text']


def test_xlsx_uses_sheet_names_and_cached_values_without_evaluating_formulas():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Revenue'
    sheet.append(['Item', 'Amount', 'Formula'])
    sheet.append(['Quarterly revenue', 9753, '=B2*2'])
    output = io.BytesIO()
    workbook.save(output)
    pages = extract(output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert 'Revenue' in pages[0]['section']
    assert '9753' in pages[0]['text']
    assert '=B2*2' not in pages[0]['text']


def test_office_archive_expansion_is_bounded():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', b'a'*(MAX_ARCHIVE+1))
    with pytest.raises(ValueError, match='expanded'):
        extract(output.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')


def test_image_ocr_worker_uses_the_packaged_binary_or_reports_missing_local_ocr():
    image = Image.new('RGB', (900,140), 'white')
    ImageDraw.Draw(image).text((35,35), 'Invoice total 8472', fill='black', font=ImageFont.load_default(size=40))
    output = io.BytesIO()
    image.save(output,format='PNG')
    file = UploadedFile(**upload('invoice.png','image/png',output.getvalue()))
    if shutil.which('tesseract'):
        passages = extract_attachments([file])
        assert '8472' in passages[0]['text']
        assert passages[0]['section'] == 'Image OCR (first frame)'
    else:
        with pytest.raises(AttachmentError):
            extract_attachments([file])


def test_file_bytes_larger_than_metadata_limit_are_supported_and_deduplicated():
    file = UploadedFile(**upload('long.txt','text/plain',b'Revenue 8472\n'*7000))
    passages = extract_attachments([file,file])
    assert len({p['document_id'] for p in passages}) == 1
    assert all(len(p['text']) <= 3000 for p in passages)
    assert '8472' in passages[-1]['text']


def test_scanned_pdf_uses_ocr_in_the_packaged_runtime():
    image = Image.new('RGB', (900,140), 'white')
    ImageDraw.Draw(image).text((35,35), 'Invoice total 8472', fill='black', font=ImageFont.load_default(size=40))
    output = io.BytesIO()
    image.save(output,format='PDF')
    file = UploadedFile(**upload('scan.pdf','application/pdf',output.getvalue()))
    if shutil.which('tesseract'):
        passages = extract_attachments([file])
        assert '8472' in passages[0]['text']
        assert passages[0]['section'] == 'Scanned PDF OCR'
    else:
        with pytest.raises(AttachmentError):
            extract_attachments([file])


def test_extraction_workers_do_not_inherit_model_credentials(monkeypatch):
    import os
    from regulatory_agent import attachments
    monkeypatch.setenv('QWEN_API_KEY','test-only-sensitive-marker')
    real_run = attachments.subprocess.run
    protected = []
    def checked_run(*args, **kwargs):
        protected.append('QWEN_API_KEY' not in (kwargs.get('env') or os.environ))
        return real_run(*args, **kwargs)
    monkeypatch.setattr(attachments.subprocess,'run',checked_run)
    passages = extract_attachments([UploadedFile(**upload())])
    assert '8472' in passages[0]['text']
    assert protected == [True]
