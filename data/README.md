# Data

Corpora and gold questions for RegAssist, moved from the `main` branch and unzipped.

```text
data/
├── RBI/
│   ├── documents/commercial_banks/   37 RBI Master Directions for commercial banks
│   ├── documents/nbfc/               35 RBI Master Directions for NBFCs
│   ├── manifest_frozen.csv           title, dates, status, RBI URL and SHA-256 for all 72 files
│   ├── catalog_overrides.csv         effective-date corrections taken from each Direction's text
│   └── gold/rbi_eval_30.csv, .json   30 gold questions with golden paragraph and page reference
└── Tax/
    ├── documents/
    │   ├── 01_Core_IncomeTax_Act_and_Rules/       Income-tax Act, 2025 and Income-tax Rules, 2026, in scope
    │   ├── 02_CBDT_Circulars_and_Notifications/   10 files, in scope
    │   ├── 03_Related_Direct_Tax_Laws/            4 Acts, in scope
    │   ├── 04_Out_of_Scope_Other_Laws/            19 Acts, refusal distractors (not indexed by default)
    │   └── 99_Duplicates/                         byte-identical copy of the Black Money Act
    ├── tax_catalog.csv               document IDs, titles, effective dates and notes
    ├── DOCUMENT_MANIFEST.md          the original sorting manifest from the tax track
    └── gold/
        ├── gold_eval_set.json, .xlsx          94 gold questions (answer, clarify and refuse)
        ├── dropped_rules1962_questions.json   the 6 removed Rules-1962 questions
        └── page_remap.csv                     old → new page numbers for 44 questions
```

All 72 RBI files match the SHA-256 hashes in `manifest_frozen.csv`.

## Corrections to the frozen RBI manifest

`catalog_overrides.csv` records dates stated in the Directions themselves:

- Files 01, 02, 03 and 04 come into effect on 1 April 2027. The manifest lists 01, 02 and 04
  as current.
- File 15 (IRACP Directions, 2025) applies until file 04 takes effect, which repeals it.
- Files 01, 02 and 03 repeal parts of file 28 (Capital Adequacy Directions, 2025) from
  1 April 2027.
- Files 20, 32 and NBFC file 59 give 1 January 2026 as their commencement date.

## Core tax documents

`01_Core_IncomeTax_Act_and_Rules` holds the two core documents, supplied separately because
they were never committed to `main`:

| File | Document | Pages |
| --- | --- | --- |
| `Income_Tax_Act_2025_as_amended_by_FA_Act_2026.pdf` | Income-tax Act, 2025 (Act 30 of 2025), as amended by the Finance Act, 2026 | 686 |
| `En-Notified-IT-Rules-2026-20-03-2026.pdf` | Income-tax Rules, 2026 (G.S.R. 198(E)), Gazette text as notified on 20 March 2026 | 976 |

They are different editions from the files the gold set was written against (674 and 424
pages). The wording of all gold evidence was found in them, but on different pages, so the
gold set's `source_doc` and `source_pages` were updated for the 44 affected questions; every
change is listed in `gold/page_remap.csv`. The Rules file is the original notification, so
it does not include any amendments made after 20 March 2026. The Act's PDF renders the rupee
sign as a backtick; the retrieval service converts it back to ₹.

The Income-tax Rules, 1962 are not in the corpus. Their 6 gold questions (category
`rules1962`) were removed from `gold_eval_set.json` and `.xlsx` and kept in
`gold/dropped_rules1962_questions.json`. The original 100-question file is in the `main`
branch.

The Excel lock file `~$gold_eval_set.xlsx` from `main` was not copied.
