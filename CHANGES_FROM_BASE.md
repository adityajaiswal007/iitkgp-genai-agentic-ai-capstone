# Changes from the agent-orchestrator branch

This workspace starts from the `agent-orchestrator` branch. Everything below is new or changed.

## New

- `data/`: RBI and tax corpora and gold sets from `main`, unzipped and organised, plus the Income-tax Act, 2025 and Income-tax Rules, 2026 supplied separately. The tax gold set drops the 6 Rules-1962 questions and has page numbers updated for the new editions (see `data/README.md`).
- `rag-service/`: the hybrid retrieval service that replaces the mock (see `rag-service/README.md`).

## Changed at the root

- `README.md`: describes the income-tax scope and the retrieval service, adds a section on what to change in each `.env` file, starts `rag-service` instead of the mock, and fixes the Docker commands.
- `.gitignore`: ignores local environments, caches and the retrieval index.

## Changed in agent-orchestrator: income-tax (TAX) support

| File | Change |
| --- | --- |
| `src/regulatory_agent/contracts.py` | `Regulator` now allows `TAX` alongside `RBI` and `SEBI`. Search arguments, passages and conversation summaries accept it. |
| `src/regulatory_agent/prompts/planner.md` | Routing rule for income-tax and direct-tax questions; keep instrument and section numbers exact and never map 1961-Act sections to the 2025 Act; GST and other indirect taxes are out of scope; personal tax computation, regime choice and planning are personal advice; concealment is rule evasion. `BOTH` is defined as RBI plus SEBI. |
| `src/regulatory_agent/prompts/shared.md` | Assistant scope covers RBI, SEBI and income tax. |
| `src/regulatory_agent/prompts/executor.md` | Evidence issued under the Income-tax Act, 1961 must be flagged in limitations, using the status reason. |
| `src/regulatory_agent/prompts/state_summary.md` | Mentions income-tax questions. |
| `src/regulatory_agent/grounding.py` | Uploaded files that mention CBDT, income tax, direct tax, the Black Money Act or Vivad se Vishwas count as regulatory evidence. The number check reads lakh and crore amounts ("₹50 lakh" = 50,00,000), so answers using Indian units are not rejected as citing absent numbers. |
| `src/regulatory_agent/agent.py` | User-facing clarify and abstain messages mention income tax. |
| `src/regulatory_agent/models.py` | Refusal messages mention income tax. The offline demo planner routes tax questions to `TAX`, refuses GST questions as out of scope and personal tax questions as personal advice, and picks `BOTH` only for RBI plus SEBI. |
| `src/regulatory_agent/tools.py` | The mock corpus gains a fictional `demo-tax` document so demo mode exercises the tax path. |
| `tests/fixtures/refusal_cases.jsonl` | RF04 ("How much income tax will I pay on my FD interest?") is still refused, now as `personal_advice` rather than `out_of_scope`, since income tax is in scope. |
| `scripts/debug_query.py` | New: runs one question through the agent and prints the evidence labels, the draft claims and the revision feedback text, which the API reports only as counts. |
| `tests/test_tax_authority.py` | New: 11 tests for the contract, routing, refusals, `BOTH`, tax uploads and lakh/crore amounts. |

All 130 original agent tests still pass (141 with the new ones).

## Not changed

- `backend-app` and `frontend-app`. The UI still brands itself "RBI/SEBI Copilot"
  (`frontend-app/src/app/layout.tsx`, `components/chat/*.tsx`) and the backend's API title
  says "RBI/SEBI Copilot API". Renaming is cosmetic and was left out to keep their Playwright
  and unit tests untouched.
