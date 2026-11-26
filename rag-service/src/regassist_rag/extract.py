"""PDF to clean page text.

Uses pypdfium2 (pure pip, no system packages). Pages without a text layer
fall back to Tesseract OCR when it is installed and enabled.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

log = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\ufffe\uffff]")
_DOT_LEADER = re.compile(r"(\.\s?){5,}|…{2,}")
_PAGE_NUMBER = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*of\s*\d{1,4})?\s*$", re.I)
_HEADING_ENTRY = re.compile(r"^\d{1,3}[A-Z]{0,2}\.\s.{3,110}\.$")


def _is_arrangement_page(lines: list[str]) -> bool:
    """An Act's 'arrangement of sections' page: mostly one-line numbered headings.

    These pages carry every section title of the Act, so they would otherwise win
    almost any keyword search while saying nothing about the law itself."""
    content = [l for l in lines if l]
    if any("ARRANGEMENT OF SECTIONS" in l.upper() for l in content[:5]):
        return True
    if len(content) < 8:
        return False
    entries = sum(1 for l in content if _HEADING_ENTRY.match(l))
    return entries >= 8 and entries / len(content) >= 0.6


_RUPEE_FONT = re.compile(r"`\s?(?=\d)")  # Rupee Foradian fonts extract the ₹ sign as a backtick


def _normalise_line(line: str) -> str:
    line = unicodedata.normalize("NFKC", line)
    line = _CONTROL.sub(" ", line)
    line = _RUPEE_FONT.sub("₹", line)
    line = line.replace("\u00ad", "").replace("\ufffe", "-")
    return re.sub(r"[ \t]+", " ", line).strip()


def _ocr_page(page) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""
    try:
        image = page.render(scale=2).to_pil()
        return pytesseract.image_to_string(image, lang="eng")
    except Exception as exc:  # OCR is best effort
        log.warning("OCR failed: %s", exc)
        return ""


def extract_pages(path: Path, ocr: bool = True) -> list[str]:
    """Return one cleaned text string per physical page (index 0 = page 1)."""
    pdf = pdfium.PdfDocument(str(path))
    raw_pages = []
    try:
        for page in pdf:
            text = page.get_textpage().get_text_range()
            if len(text.strip()) < 30 and ocr:
                text = _ocr_page(page) or text
            raw_pages.append(text)
    finally:
        pdf.close()

    pages = [[_normalise_line(l) for l in re.split(r"\r\n|\r|\n", text)] for text in raw_pages]

    # Remove running headers/footers: short lines repeated on many pages.
    if len(pages) >= 5:
        counts = Counter(l for lines in pages for l in set(lines) if l and len(l) <= 120)
        threshold = max(3, int(0.4 * len(pages)))
        repeated = {l for l, c in counts.items() if c >= threshold}
    else:
        repeated = set()

    cleaned = []
    for lines in pages:
        if _is_arrangement_page(lines):
            cleaned.append("")
            continue
        kept = []
        for line in lines:
            if not line or line in repeated or _PAGE_NUMBER.match(line):
                continue
            if _DOT_LEADER.search(line):  # table-of-contents rows
                continue
            kept.append(line)
        cleaned.append("\n".join(kept))
    return cleaned
