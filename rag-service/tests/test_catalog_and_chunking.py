from regassist_rag.catalog import Catalog
from regassist_rag.chunking import chunk_document
from regassist_rag.lexical import tokenize

from conftest import DATA_DIR


def test_catalog_covers_both_corpora_with_unique_ids():
    catalog = Catalog.load(DATA_DIR)
    indexable = catalog.indexable()
    assert sum(d.authority == "RBI" for d in indexable) == 72
    assert sum(d.authority == "TAX" for d in indexable) == 16
    assert len({d.document_id for d in indexable}) == len(indexable)
    assert all((DATA_DIR / d.path).exists() for d in catalog.documents.values())


def test_out_of_scope_and_duplicate_documents_are_not_indexed_by_default():
    catalog = Catalog.load(DATA_DIR)
    ids = {d.document_id for d in catalog.indexable()}
    assert not any(i.startswith(("tax-oos-", "tax-dup-")) for i in ids)
    assert any(d.document_id.startswith("tax-oos-") for d in catalog.indexable(include_out_of_scope=True))
    # gold references to the duplicate file resolve to the original Black Money Act
    assert catalog.by_filename["A2015-22 (1).pdf"] == catalog.by_filename["A2015-22.pdf"] == "tax-act-2015-22"


def test_core_tax_documents_are_in_force_from_april_2026_and_keep_gold_aliases():
    catalog = Catalog.load(DATA_DIR)
    act, rules = catalog.get("tax-act-2025"), catalog.get("tax-rules-2026")
    assert act.effective_from == rules.effective_from == "2026-04-01"
    assert catalog.by_filename["Income-tax-Act-2025_2026_2026-08-25_10-41-45_a1d3cd_en.pdf"] == "tax-act-2025"
    assert catalog.by_filename["Income-tax-Rules-2026_2026-10-23_11-50-47_59f727_en.pdf"] == "tax-rules-2026"


def test_commencement_overrides_correct_the_frozen_manifest():
    catalog = Catalog.load(DATA_DIR)
    new = catalog.get(catalog.by_filename["04_CB_AssetClassificationProvisioning_2026.pdf"])
    old = catalog.get(catalog.by_filename["15_CB_IRACP_2025_upd2026-10-01.pdf"])
    cva = catalog.get(catalog.by_filename["01_CB_CreditValuationAdjustment_2026.pdf"])
    assert new.effective_from == "2027-04-01"
    assert old.effective_to == "2027-04-01" and old.superseded_by == new.document_id
    assert cva.effective_from == "2027-04-01"


def test_chunks_follow_paragraph_numbers_and_never_cross_pages():
    pages = [
        "CHAPTER II – CONDUCT\n18. The add-on cards shall be issued only to identified persons.\n"
        "19. Any request for closure of a credit card shall be honoured within seven working days.",
        "Cardholders shall be notified about the closure.\n20. If a card is not used for over a year, the issuer may close it.",
    ]
    chunks = chunk_document("rbi-001-x", "RBI", pages, target_chars=60, max_chars=200)
    assert {c.page for c in chunks} == {1, 2}
    first_page_two = next(c for c in chunks if c.page == 2)
    assert first_page_two.section == "para 19 (contd.)"
    assert any(c.section == "para 20" for c in chunks)
    assert all(c.heading.startswith("CHAPTER II") for c in chunks)


def test_numbered_list_items_and_annexes_do_not_break_rule_numbering():
    pages = [
        "12. Every application shall contain the following particulars:\n1. name of the applicant;\n2. address.",
        "13. The application shall be disposed of within six months.",
        "APPENDIX I\n1. Particulars of the deductor, including name, address and tax deduction account number.",
    ]
    chunks = chunk_document("tax-rules-x", "TAX", pages, target_chars=40, max_chars=400, unit="rule")
    assert [c.section for c in chunks[:2]] == ["rule 12", "rule 13"]
    assert chunks[-1].section == "item 1" and chunks[-1].heading == "APPENDIX I"


def test_cross_references_are_not_mistaken_for_annex_headings():
    pages = ["5. The rate shall be as specified in the\nSchedule II (Table: Sl. No. 2) does not apply,\n"
             "6. Where the assessee is a resident individual, the deduction shall be allowed in full."]
    chunks = chunk_document("tax-act-x", "TAX", pages, target_chars=40, max_chars=400, unit="section")
    assert [c.section for c in chunks] == ["section 5", "section 6"]


def test_long_blocks_are_split_within_the_limit():
    pages = ["5. " + "The bank shall comply with this requirement. " * 200]
    chunks = chunk_document("rbi-001-x", "RBI", pages, target_chars=500, max_chars=800, overlap_chars=200)
    assert len(chunks) > 5
    assert max(len(c.text) for c in chunks) <= 800 + 200 + 2


def test_tokenizer_handles_rupees_stems_and_keeps_numbers():
    assert tokenize("₹500 per day")[:2] == ["rs", "500"]
    assert "year" in tokenize("five years.")  # 'rs.' rule must not split 'years.'
    assert tokenize("closing") == tokenize("closes")


def test_scanned_indian_amounts_are_repaired_but_dates_and_decimals_are_not():
    from regassist_rag.extract import _normalise_line
    assert _normalise_line("Before High Court 1,00.00.000") == "Before High Court 1,00,00,000"
    assert _normalise_line("Before Supreme Court 2.00.00,000") == "Before Supreme Court 2,00,00,000"
    assert _normalise_line("dated 11.07.2018") == "dated 11.07.2018"
    assert _normalise_line("at 2.00 per cent") == "at 2.00 per cent"
    assert _normalise_line("Upto ` 400000 Nil") == "Upto ₹400000 Nil"


def test_overlap_keeps_a_table_split_at_a_chunk_boundary_together():
    # The CBDT Circular 17/2019 case: the limits table straddled two chunks.
    pages = ["2. The table shall read as follows. " + "Background text. " * 70 +
             "1. Before Appellate Tribunal 50,00,000 2. Before High Court 1,00,00,000\n"
             "3. Before Supreme Court 2,00,00,000 and the remaining text continues here."]
    chunks = chunk_document("tax-x", "TAX", pages, target_chars=1200, max_chars=1300, overlap_chars=250)
    assert len(chunks) == 2
    assert all(a in chunks[1].text for a in ("50,00,000", "1,00,00,000", "2,00,00,000"))
    assert chunks[1].text.startswith("… ")


def test_chunks_fit_in_the_agents_passage_window():
    import pytest
    from regassist_rag.config import Settings
    with pytest.raises(ValueError):
        Settings(chunk_max_chars=1700, chunk_overlap_chars=250)
