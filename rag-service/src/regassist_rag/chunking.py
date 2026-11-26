"""Structure-aware chunking for regulatory text.

RBI directions are numbered by paragraph ("19. Any request ...") and acts by
section ("2. Definitions.—"). Chunks start at those boundaries where possible,
never cross a page (so every citation has one page number), and carry the
nearest chapter heading and paragraph/section number as their label.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

CHUNKER_VERSION = "10"

_PARA = re.compile(r"^(\d{1,3}[A-Z]?)\.\s*(?=[A-Z(\"“‘'])")          # 19. Any request / 2. Definitions.—
_CHAPTER = re.compile(r"^(CHAPTER|Chapter)\s*[-–—:]?\s*([IVXLC]+|\d+)\b[\s:.\-–—]*(.{0,90})")
_PART = re.compile(r"^(PART|Part)\s+([IVXLC]+|[A-Z])\b[\s:.\-–—]*(.{0,90})")
# Annex, schedule, appendix and form headings, but not cross-references such as
# "Schedule II (Table: Sl. No. 2) does not apply" or "Annex IX, sworn before".
_ANNEX = re.compile(
    r"^(?:THE\s+[A-Z]+\s+SCHEDULE"
    r"|(?:SCHEDULE|APPENDIX|ANNEX(?:URE)?)\s*[-–—]?\s*[IVXLC\d]+[A-Z]?"
    r"|FORM\s+NO\.?\s*\d+[A-Z]*"
    r"|Annex(?:ure)?\s*[-–—]?\s*[IVXLC\d]+[A-Z]?(?:\s*[-–—:]\s*\S.*)?"
    r"|(?:Appendix|Schedule)\s*[-–—]?\s*[IVXLC\d]+[A-Z]?\s*[-–—:]\s*\S.*"
    r")\s*$")
_SENTENCE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"“])")


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    authority: str
    page: int
    ordinal: int
    section: str
    heading: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _split_long(text: str, max_chars: int) -> list[str]:
    """Split at sentence ends, then at spaces, so no piece exceeds max_chars."""
    if len(text) <= max_chars:
        return [text]
    pieces, current = [], ""
    for sentence in _SENTENCE.split(text):
        while len(sentence) > max_chars:  # run-on text such as tables
            if current:
                pieces.append(current)
                current = ""
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


def _overlap_tail(text: str, size: int) -> str:
    """The last `size` characters of text, starting at a word boundary."""
    if size <= 0 or len(text) <= size:
        return text if size > 0 else ""
    tail = text[-size:]
    space = tail.find(" ")
    return tail[space + 1:] if 0 <= space < size // 2 else tail

def _number(value: str) -> int:
    return int(re.match(r"\d+", value).group())


def _plausible_next(candidate: str, current: str | None) -> bool:
    """Paragraph, section and rule numbers increase. A smaller number, or a jump of more
    than 25, is a numbered list item or table row inside the current unit."""
    if current is None:
        return True
    new, old = _number(candidate), _number(current)
    if new == old:
        return candidate != current  # 12 -> 12A
    return old < new <= old + 25


def _label(unit: str, numbers: list[str], continued: bool) -> str:
    numbers = [n for n in numbers if n]
    if not numbers:
        return ""
    word = unit
    if len(numbers) > 1 and _number(numbers[-1]) > _number(numbers[0]):
        label = f"{word}s {numbers[0]}–{numbers[-1]}"
    else:
        label = f"{word} {numbers[0]}"
    return label + (" (contd.)" if continued else "")


def chunk_document(document_id: str, authority: str, pages: list[str],
                   target_chars: int = 1200, max_chars: int = 1500, unit: str = "para",
                   overlap_chars: int = 250) -> list[Chunk]:
    """unit names the numbered division in labels: 'para', 'section' or 'rule'.

    Each chunk after the first on a page starts with the last ~overlap_chars of the
    previous chunk, so a table row or sentence cut at a boundary appears whole in at
    least one chunk. Chunks never exceed max_chars + overlap_chars."""
    chunks: list[Chunk] = []
    heading, current_number, in_annex = "", None, False

    for page_index, page_text in enumerate(pages, start=1):
        lines = [l for l in page_text.split("\n") if l.strip()]
        if not lines:
            continue
        # Blocks: [number, continued-from-previous-page, text lines, heading, label word]
        blocks: list[list] = []
        for line in lines:
            annex = bool(_ANNEX.match(line)) and len(line) < 120
            if (_CHAPTER.match(line) or _PART.match(line) or annex) and len(line) < 140:
                heading = line[:120]
                if annex:
                    # Annexes, schedules and forms restart their numbering; their numbered
                    # items are not paragraphs, sections or rules.
                    current_number, in_annex = None, True
                elif _CHAPTER.match(line):
                    in_annex = False
            word = "item" if in_annex else unit
            para = _PARA.match(line)
            if para and len(line) > 8 and _plausible_next(para.group(1), current_number):
                current_number = para.group(1)
                blocks.append([current_number, False, [line], heading, word])
            elif not blocks:
                blocks.append([current_number, current_number is not None, [line], heading, word])
            else:
                blocks[-1][2].append(line)

        # Pack blocks into chunks of roughly target size without crossing the page.
        packed: list[tuple[list, bool, str, str, str]] = []
        for number, continued, block_lines, block_heading, word in blocks:
            text = " ".join(block_lines)
            if packed and packed[-1][4] == word and len(packed[-1][2]) + len(text) + 1 <= target_chars:
                n, c, t, h, w = packed[-1]
                packed[-1] = (n + [number], c, t + "\n" + text, h, w)
            else:
                packed.append(([number], continued, text, block_heading, word))

        for numbers, continued, text, block_heading, word in packed:
            for piece_index, piece in enumerate(_split_long(text, max_chars)):
                if (len(piece) < 40 and chunks and chunks[-1].page == page_index
                        and len(chunks[-1].text) + len(piece) + 1 <= max_chars + overlap_chars):
                    chunks[-1].text += "\n" + piece
                    continue
                if overlap_chars and chunks and chunks[-1].page == page_index:
                    piece = "… " + _overlap_tail(chunks[-1].text, overlap_chars) + "\n" + piece
                ordinal = len(chunks)
                label = _label(word, numbers if piece_index == 0 else numbers[:1], continued or piece_index > 0)
                chunks.append(Chunk(
                    chunk_id=f"{document_id}:p{page_index}:c{ordinal}",
                    document_id=document_id,
                    authority=authority,
                    page=page_index,
                    ordinal=ordinal,
                    section=label or f"page {page_index}",
                    heading=block_heading,
                    text=piece,
                ))
    return chunks
