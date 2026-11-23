# Income-Tax Copilot — Scope Definition (Track B, Task B)

**Role:** Domain spec & eval design · **Status:** frozen with the gold set (Week-1 gate) · **Domain:** Indian direct tax (income-tax) document Q&A

## 1. What the copilot does

A citation-grounded question-answering assistant over a fixed corpus of Indian income-tax legal documents. Given a natural-language question from a taxpayer, practitioner or finance-ops user, it:

1. Decides whether the question is **answerable from the corpus** (`answer`), **needs clarification** (`clarify`), or **must be declined** (`refuse`).
2. If answerable, returns a short, correct answer **with citations** (document, page, verbatim quote).
3. Always states the **legal regime** it relied on (Act 2025 / Rules 2026 vs. legacy 1961 Act / Rules 1962) when that affects the answer.

It is a *lookup-and-explain* tool for statutory text. It is **not** a tax adviser, return-filing tool, or case-law database.

## 2. Corpus (after sorting)

37 PDFs were reviewed; 17 are in scope (+1 byte-identical duplicate parked; 19 are out of scope). Folder: `TaxDocuments/`.

| Folder | Docs | In scope? | Role |
|---|---|---|---|
| `01_Core_IncomeTax_Act_and_Rules` | Income-tax Act 2025 (674 pp, as amended by Finance Act 2026); Income-tax Rules 2026 (424 pp); Income-tax Rules 1962 (628 pp) | **Yes – primary** | Current law (Act 2025 + Rules 2026) and legacy rules |
| `02_CBDT_Circulars_and_Notifications` | Circulars 3/2018, 17/2019; Notifications 97/2016, 24/2018, 71/2018, 81/2020, 23/2021, 25/2021, 41/2022, 75/2014 | **Yes – secondary** | Administrative instructions issued under the **1961 Act** |
| `03_Related_Direct_Tax_Laws` | Black Money Act 2015; Direct Tax Vivad se Vishwas Act 2020; National Tax Tribunal Act 2005; Tribunals Reforms Act 2026 | **Yes – adjacent** | Direct-tax-adjacent statutes |
| `04_Out_of_Scope_Other_Laws` | 19 unrelated Acts (CGST, Companies, IBC, Aadhaar, MSME, Energy Conservation, …) | **No** | Kept as *distractors*: the retriever must not surface them; questions about them are refusals |
| `99_Duplicates` | `A2015-22 (1).pdf` (identical to the Black Money Act) | No | Remove from index to avoid double-counting in recall@k |

Total in-scope text ≈ 4.9 M characters (1,942 pages).

## 3. The key scoping trap: two legal regimes

* The **Income-tax Act, 2025** (30 of 2025) **commences 1 April 2026** and **repeals the 1961 Act** (s.536). Proceedings for tax years beginning before 1 April 2026 continue under the repealed Act (s.536(2)(c)).
* **The 1961 Act itself is not in the corpus.** The Rules 1962 and all circulars/notifications cite 1961-Act section numbers (e.g. s.143(2), s.10(46), s.268A, s.120).
* Consequences for the copilot:
  * Terminology differs: 2025 Act uses **"tax year"**, not "assessment year / previous year". The word "assessment year" appears **0 times** in the 2025 Act text.
  * A question about "section 80C" (1961) is **not answerable** from the corpus — the 2025 Act has a different numbering. The copilot must say so rather than guess a limit.
  * Circular/notification answers must be labelled "issued under the 1961 Act; check current applicability".
  * Where Rules 2026 and Rules 1962 differ on the same topic, the answer follows the regime named by the user; if unspecified, default to **Act 2025 + Rules 2026** and say so.

## 4. In scope

| # | Capability | Example |
|---|---|---|
| S1 | Definitions and applicability in Act 2025 / Rules 2026 | "How does the Act define 'tax year'?" |
| S2 | Numeric lookups: rates, thresholds, limits, deadlines, form numbers | "What is the time limit for …?" |
| S3 | Enumerations (lists of heads, exemptions, conditions) | "Which incomes are exempt under …?" |
| S4 | Multi-hop within or across documents | "Which forum for departmental appeal above Rs 50 lakh and what is the limit?" |
| S5 | Circulars/notifications: authority, limit, scope, effective dates, supersession status | "Which authority was designated under notification X?" |
| S6 | Related direct-tax statutes (Black Money, Vivad se Vishwas, NTT, Tribunals Reforms) | "What penalty applies for failure to furnish foreign asset details?" |
| S7 | Regime disambiguation (old vs new law) | "Under the old Rules 1962, what was …?" |
| S8 | Clarification of ambiguous questions | "What is the limit?" → ask which limit |

## 5. Out of scope (→ refuse; details in `02_Refusal_Policy.md`)

* Indirect taxes (GST/CGST) and non-tax laws in `04_Out_of_Scope_Other_Laws`.
* Case law, tribunal/court rulings, press commentary, forecasts (Budget/future amendments).
* Personalised tax advice, computing a specific person's liability, tax planning/structuring, drafting notice replies.
* Anything that facilitates tax evasion, concealment or fraud.
* Personal data lookups (PAN, addresses, returns of identified persons).
* e-filing portal how-to, refund/notice status, software support.
* Content from the 1961 Act text itself (repealed; not in corpus).

## 6. What a correct answer looks like

An answer is **correct** only if all of the following hold (graded separately — see `03_Prompt_Engineering_Report.md` §4):

1. **Decision correct:** `answer` / `clarify` / `refuse` matches the gold decision.
2. **Facts correct:** every atomic *key fact* in the gold answer is present (numbers, dates, section/rule/form numbers match exactly after normalisation, e.g. `Rs. 50,00,000` = `5000000`).
3. **No contradicting or invented facts** (hallucination check).
4. **Grounded:** at least one citation whose quoted text is a verbatim substring of the cited document page. A correct answer with no valid citation scores **partial**.
5. **Regime stated** where relevant (Act 2025 vs 1961-era instrument).
6. **Concise:** ≤ 120 words of answer text, no legal advice language ("you should…").

## 7. Users, assumptions, boundaries

* **Users:** salaried/business taxpayers, small-business finance staff, CAs/tax associates. English only. Non-English text in the corpus (Hindi gazette headers in Notification 23/2021) is ignored.
* **Time reference:** corpus is a snapshot (Act text dated 2026-08-25, Rules 2026 dated 2026-10-23). The copilot never claims to be "current" beyond the snapshot.
* **Human-in-the-loop boundary:** the copilot never files, signs, pays, replies to notices or asserts a person's liability; output is informational only and carries a one-line disclaimer.
* **Latency/cost targets (for later weeks):** p95 ≤ 8 s, ≤ 3 k prompt tokens of retrieved context, ≤ 10 agent steps.

## 8. Evaluation design (summary)

* **100 questions** frozen *before* any agent code: 80 answerable (grounded in 17 docs) + 20 refusal/clarify/false-premise.
* Every answerable question has gold answer, atomic key facts, source doc, page(s) and a **machine-verified verbatim evidence quote**.
* Distribution and tags are in `data/gold_eval_set.xlsx` (sheet *Summary*).
* Splits: `dev` (≈ 30 %, for prompt iteration) and `test` (≈ 70 %, touched only for final numbers) — see the `split` field.
