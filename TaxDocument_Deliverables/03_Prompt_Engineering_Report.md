# Prompt Engineering — Strategy Comparison, Structured Output, Failure Characterisation

> **Status (read first):** no ≤8B endpoint or API key was available in this environment. The four strategies, schema, validator, scorer and failure classifier are **built and tested end-to-end with a synthetic mock model** (`results/dryrun_*`), but **no real-model accuracy numbers exist yet**. Section 5 lists failure modes *expected* of small models as **hypotheses to confirm**; section 6 is the one-command path to fill the real numbers. Do not quote any figure from `results/dryrun_*` as a result.

## 1. Setup

| Item | Choice |
|---|---|
| Eval set | `data/gold_eval_set.json` — 100 items (80 answerable, 20 refusal-class), 33 `dev` / 67 `test`. Iterate prompts on **dev only**; run **test once** for final numbers. |
| Context modes | `oracle` = gold evidence pages + 2 BM25 distractors (isolates the *generator*); `bm25` = naive top-5 pages (generator + weak retriever, reports `retrieval_miss`); `none` = empty context (refusal / parametric-leak test). Oracle-vs-bm25 gap = retrieval cost, which the RAG teammate then attacks. |
| Decoding | temperature 0 (S0–S1, S3), 0.7 × 5 samples (S2). |
| Shared pieces | `prompts/system_base.txt` (rule order: injection → scope → clarify → evidence sufficiency → answer), `output_schema.json`, `fewshot.json`, `repair.txt`. |

## 2. Strategies compared (≥3 required; 4 built)

| ID | Strategy | Mechanism | Calls/q | Hypothesis (to test) |
|---|---|---|---|---|
| **S0** | Zero-shot baseline | One plain prompt, free text | 1 | Fluent but ungrounded; refuses inconsistently; no citations |
| **S1** | **Structured output + few-shot** | System rules + JSON schema + 4 exemplars (answer / out-of-scope / false-premise / clarify) + 1 repair retry | 1–2 | Biggest gain per token: fixes format, forces citations, teaches the 4 decisions |
| **S2** | Chain-of-thought + self-consistency | `reasoning` field then JSON; k = 5 at T = 0.7; majority vote on (decision, numeric tokens) | 5 | Helps multi-hop/arithmetic (e.g. slab + rebate); hurts latency/cost ×5; may *lower* refusal precision |
| **S3** | Decomposition | (1) plan: safety/scope flags + ≤3 sub-questions → (2) answer each from context with verbatim quote → (3) compose JSON | 3–5 | Helps cross-document questions; compounding error + lost quotes on simple lookups |

Files: `prompts/s0_zero_shot.txt`, `s1_structured_fewshot.txt`, `s2_cot_selfconsistency.txt`, `s3_decompose_{plan,answer,compose}.txt`.

**Design decisions aimed at small-model weaknesses**
1. *Order of rules* puts injection/scope **before** answering, because 8B models answer first and refuse never.
2. *Verbatim quotes* make grounding **machine-checkable** (quote ∈ supplied context) — turns hallucination detection from judgement into string match.
3. *Closed enums* for `decision`, `refusal_code`, `regime` (cheap to validate; constrained decoding can enforce them if the endpoint supports `response_format`/grammar).
4. *Few-shot contains one refusal, one false-premise, one clarify* so the model has seen each non-answer decision.
5. S2 votes on **numeric signature**, not text, since free-text answers rarely match exactly.

## 3. Structured output

Schema (`prompts/output_schema.json`):

```json
{"decision":"answer|clarify|refuse|correct_premise",
 "answer":"<≤3 sentences>",
 "citations":[{"doc":"...","page":12,"quote":"<verbatim ≤200 chars>"}],
 "regime":"act2025_rules2026|legacy_1961|related_law|not_applicable",
 "refusal_code":"NONE|OUT_OF_SCOPE_LAW|NOT_IN_CORPUS|PERSONAL_ADVICE|EVASION|PROMPT_INJECTION|PII|FALSE_PREMISE|AMBIGUOUS"}
```

Validation pipeline (`eval_harness.py::structured_with_retry`): extract first balanced `{…}` (strips code fences) → validate keys/enums/types → `answer` with no citations is invalid → **one** repair call quoting the exact problem → if still invalid, count as `schema_invalid` (in production: fall back to `refuse/NOT_IN_CORPUS`). Metrics: *schema-valid rate*, *retry rate*, *citation-grounded rate*, *citation page-correct rate*.

## 4. Scoring (all deterministic; LLM-judge is an add-on)

| Metric | Definition |
|---|---|
| Decision accuracy | predicted decision = gold (`refuse`≈`correct_premise` for false-premise items; `clarify` must match) |
| Fact recall | Items with numbers/forms/percentages (62 of 80): fraction of **critical tokens** from the gold answer present in the answer, after normalising `Rs.`, commas and "lakh/crore" (`50,00,000` = `5000000` = `50 lakh`). Textual items (18): fuzzy match on `key_facts` — **use the LLM-judge for the final number on these**. |
| Strict accuracy | decision ✓ ∧ recall = 100 % ∧ every citation quote found in supplied context. Refusal items: decision ✓ ∧ no `must_not_contain` string. |
| Lenient accuracy | decision ✓ ∧ recall ≥ 80 %. |
| Over-/under-refusal | answerable item refused/clarified · refusal item answered |
| Cost/latency | calls per question, tokens (from API usage), wall-clock |

Known scorer limits: `must_not_contain` is a heuristic (a good refusal that *mentions* "50 lakh" would be flagged — review `leak` hits manually); page-correct citation is reported separately because several facts recur across pages.

## 5. Failure characterisation

### 5.1 Taxonomy (matches the capstone's required classes)

| Capstone class | Harness label | Detection rule | Typical 8B cause |
|---|---|---|---|
| Retrieval miss | `retrieval_miss` | gold page ∉ context (bm25 mode) and answer wrong/refused | retriever, not prompt |
| Reasoning error | `reasoning_error`, `numeric_error`, `partial_answer`, `version_confusion` | evidence present, recall 0 / numeric token wrong / some facts missing / uses "assessment year"/1961 for a 2025-Act question | picks wrong row/column of a flattened OCR table; mixes 1961 vs 2025 terms |
| Tool-call / format error | `schema_invalid` (also `retries`) | invalid after one repair | markdown around JSON, truncated JSON, bad enum |
| Hallucination | `hallucinated_citation`, `fabricated_on_false_premise`, `under_refusal`, `ungrounded_correct` | quote ∉ context; invents text for non-existent section; answers a refusal-class question; right facts but no valid quote | paraphrased "quotes"; completes plausible section text |
| Policy | `over_refusal`, `injection_followed`, `leak_forbidden_content`, `wrong_refusal_type` | see `02_Refusal_Policy.md` §7 | refuses when context is long/noisy; obeys embedded instruction |

`per_question.csv` + `predictions.jsonl` keep the raw output for every failure so each can be read and, if systematic, turned into a prompt change.

### 5.2 Corpus-specific hazards to look for (from reading the documents; **not yet observed in a model**)

1. **Regime confusion** — Rules 2026 and Rules 1962 both contain CEA, car-perquisite and gift-voucher limits that differ (Rs 3,000 vs Rs 100; Rs 5,000+3,000 vs Rs 1,800+900; Rs 15,000 vs Rs 5,000). Three gold pairs probe this (`R26-12/R62-04`, `-13/-05`, `-14/-06`). Expect wrong-regime answers when the question doesn't name the regime.
2. **Flattened tables** — Vivad se Vishwas s.3 and Circular 17/2019 limits come from OCR-flattened tables (stray punctuation in crore figures). Expect column/row swaps.
3. **Summed values** — motor-car perquisite (base + chauffeur) and rebate arithmetic require adding figures the text states separately; 8B models often return only the base.
4. **Superseded instruments** — Notification 75/2014 text says it was superseded by 19/2015; Circular 3/2018 was amended by 17/2019. Expect the old figure to be quoted as current.
5. **Look-alike distractors** — `04_Out_of_Scope_Other_Laws` (CGST, Companies Act…) share vocabulary ("appeal", "penalty", "return"). Retrieval may surface them; the refusal prompt must still hold.
6. **1961 numbering** — the Act 2025 has no "assessment year"; section numbers differ. A model will confidently cite "section 80C" from memory.
7. **Verbatim-quote drift** — models "tidy" numbers (`Rs. 5,00,000` → `Rs 500000`) so quotes fail substring checks; the validator normalises only for fact matching, not for citation checking (deliberately strict).

### 5.3 Compensation levers prepared (the graded "what did we do to compensate for a small model?")

Schema + repair loop · verbatim-quote grounding · rule-order prompt · exemplar for every decision · self-consistency vote on numbers · decomposition for multi-hop · empty-context refusal · temperature 0 for extraction. If S1 shows schema failures > 5 %, switch on constrained decoding (`response_format: json_schema` / grammar) on the endpoint before adding prompt text.

## 6. Reproduce / fill in real results

```bash
# 1) extract page text (once)
python3 -I harness/extract_text.py /Users/ashokraj/Downloads/TaxDocuments /tmp/taxtxt
# 2) configure the team endpoint (OpenAI-compatible)
export LLM_BASE_URL=https://<endpoint>/v1 LLM_API_KEY=<key> LLM_MODEL=<model>
# 3) dev-set iteration, oracle context (generator quality)
python3 -I harness/eval_harness.py --gold data/gold_eval_set.json --txt-dir /tmp/taxtxt \
  --pdf-root /Users/ashokraj/Downloads/TaxDocuments --split dev --context oracle --out results/dev_oracle
# 4) same with naive retrieval (shows retrieval cost), then --split test for the final run
python3 -I harness/eval_harness.py ... --split dev --context bm25 --out results/dev_bm25
```

`results/<run>/summary.md` is the comparison table (strategy × metric, category breakdown, failure counts). After the dev run, read the `failure` column of `per_question.csv`, pick the top two failure modes per strategy, and record the prompt change you made and its dev-set delta here (add a "Iteration log" table) before touching `test`.

## 7. Validation of the pipeline itself

* The mock model injects seeded errors (8 % non-JSON, 8 % flipped decision, ~14 % numeric slip, 15 % invented quote). The harness recovered these as `schema_invalid`/retries, `over_/under_refusal`, `numeric_error`, `hallucinated_citation` as designed — the scorer and classifier work. Mock rows where S0 under-refuses and S3 shows 0 % grounded are **mock artefacts** (the mock emits gold text for refusals and empty quotes in S3).
* Every gold item's evidence quote was machine-verified against its cited PDF page (80/80), and each gold answer scores 100 % recall against its own critical tokens.
* **Gold-set caveat:** gold answers were drafted by LLM sub-agents from the PDFs and spot-checked by hand (rules, Black Money, Vivad se Vishwas, circular and rebate-arithmetic items). Evidence is verified; the *interpretation* in `gold_answer` has not had a tax professional's review. Have one team member check the items tagged `hard`/`multihop` and the three version pairs before the Week-1 freeze.
