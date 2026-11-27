# Changes from the agent-orchestrator branch

This workspace starts from the `agent-orchestrator` branch. Everything below is new or changed.

## New

- `data/`: RBI and tax corpora and gold sets from `main`, unzipped and organised, plus the Income-tax Act, 2025 and Income-tax Rules, 2026 supplied separately. The tax gold set drops the 6 Rules-1962 questions and has page numbers updated for the new editions (see `data/README.md`).
- `data/RBI/gold/CORRECTIONS.md`: two RBI gold questions labelled unanswerable are answered by the corpus; corrected to lookups.
- `rag-service/`: the hybrid retrieval service that replaces the mock (see `rag-service/README.md`).
- `observability/langfuse/`: the official Langfuse Docker setup with a port override and a `.env` generator, for the trace dashboard (see its README).

## Changed at the root

- `README.md`: describes the income-tax scope and the retrieval service, adds a section on what to change in each `.env` file, starts `rag-service` instead of the mock, and fixes the Docker commands.
- `.gitignore`: ignores local environments, caches and the retrieval index.

## Changed in agent-orchestrator: income-tax (TAX) support

| File | Change |
| --- | --- |
| `src/regulatory_agent/contracts.py` | `Regulator` now allows `TAX` alongside `RBI` and `SEBI`. Search arguments, passages and conversation summaries accept it. |
| `src/regulatory_agent/prompts/planner.md` | Routing rule for income-tax and direct-tax questions; keep instrument and section numbers exact and never map 1961-Act sections to the 2025 Act; GST and other indirect taxes are out of scope; personal tax computation, regime choice and planning are personal advice; concealment is rule evasion. `BOTH` is defined as RBI plus SEBI. |
| `src/regulatory_agent/prompts/planner.md` (clarification) | RBI topics include loans, lending, interest rates and deposits. Clarify only for a fact about the user's situation that changes the answer; never ask which regulation or document applies, since finding it is the search's job. |
| `src/regulatory_agent/prompts/critic.md` | Never ask which regulation or document applies; request a planner revision instead. Applicability fails when a claim applies a rule outside the sub-section named in its passage's section label. |
| `src/regulatory_agent/prompts/executor.md` (scope) | Apply a passage's rule only to what its sub-section covers (benchmark, product or entity), never to a different one. |
| `src/regulatory_agent/prompts/shared.md` | Assistant scope covers RBI, SEBI and income tax. |
| `src/regulatory_agent/prompts/executor.md` | Evidence issued under the Income-tax Act, 1961 must be flagged in limitations, using the status reason. |
| `src/regulatory_agent/prompts/state_summary.md` | Mentions income-tax questions. |
| `src/regulatory_agent/grounding.py` | Uploaded files that mention CBDT, income tax, direct tax, the Black Money Act or Vivad se Vishwas count as regulatory evidence. The number check reads lakh and crore amounts ("₹50 lakh" = 50,00,000), so answers using Indian units are not rejected as citing absent numbers. |
| `src/regulatory_agent/agent.py` | User-facing clarify and abstain messages mention income tax. |
| `src/regulatory_agent/models.py` | Refusal messages mention income tax. The offline demo planner routes tax questions to `TAX`, refuses GST questions as out of scope and personal tax questions as personal advice, and picks `BOTH` only for RBI plus SEBI. |
| `src/regulatory_agent/tools.py` | The mock corpus gains a fictional `demo-tax` document so demo mode exercises the tax path. |
| `tests/fixtures/refusal_cases.jsonl` | RF04 ("How much income tax will I pay on my FD interest?") is still refused, now as `personal_advice` rather than `out_of_scope`, since income tax is in scope. |
| `src/regulatory_agent/tracing.py` | New: optional Langfuse tracing, off unless `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. One trace per question with a span per graph node, a generation per Qwen call, a retriever or tool span per retrieval call, and events for the critic's verdict and for early stops. |
| `src/regulatory_agent/agent.py` (tracing) | Graph nodes, model calls, tool calls, the critic verdict and stops report to `tracing`. |
| `src/regulatory_agent/api.py` (tracing) | Each request is a fresh trace named `regassist-query`, with the conversation ID as the Langfuse session; buffered traces are sent on shutdown. |
| `src/regulatory_agent/contracts.py` | `call_with_timeout` copies context variables into its worker thread, so model and tool spans nest under their step. |
| `pyproject.toml`, `.env.example` | Add the `langfuse` package and the three optional `LANGFUSE_` settings. |
| `tests/test_tracing.py` | New: 7 tests with spans captured in memory (no server needed), covering trace structure, rejection reasons, public traces, the trace-ID header and the showcase script. |
| `src/regulatory_agent/api.py` (trace links) | Responses carry the trace ID in an `X-Trace-Id` header. With `LANGFUSE_PUBLIC_TRACES=1` each trace is published (viewable by link without a login) and its link is printed in the agent's terminal; `LANGFUSE_PRINT_TRACE_URLS=1` prints links without publishing. |
| `scripts/trace_showcase.py` | New: runs ten showcase questions in one conversation (one Langfuse session) and writes their trace links to `showcase/<session>.md` for the report. |
| `src/regulatory_agent/agent.py` (context check) | In a conversation with history, terms the contextualizer returns that appear in the current question are no longer treated as references needing verification; only the other referents must appear in an earlier answer (compared ignoring case, hyphens and spacing). With Qwen3-8B, self-contained questions asked mid-conversation were otherwise stopped with `INVALID_CONTEXT`. Two tests in `tests/test_conversation_context.py`. |
| `src/regulatory_agent/attachments.py` (Windows fix) | The attachment-extraction subprocess now runs with UTF-8 input and output and, on Windows, with `SYSTEMROOT` and the temp folder in its environment. Before, on Windows it could fail to start, and could not return text containing ₹ or Devanagari. |
| `tests/test_prompt_workflow.py`, `tests/test_agent.py`, `tests/test_qwen_tls.py` (Windows fix) | Read and write test files as UTF-8, so the tests pass on Windows. |
| `scripts/eval_e2e.py` | New: end-to-end evaluation. Runs every gold question through the agent in a fresh conversation, grades decision, correctness (LLM judge plus deterministic checks), citations and forbidden content, assigns a failure-taxonomy category, writes `summary.md`, `failures.md` and `graded.csv`, and attaches scores to Langfuse traces. Resumable. 14 tests in `tests/test_eval_e2e.py`. |
| `scripts/debug_query.py` | New: runs one question through the agent and prints the evidence labels, the draft claims and the revision feedback text, which the API reports only as counts. |
| `tests/test_tax_authority.py` | New: 11 tests for the contract, routing, refusals, `BOTH`, tax uploads and lakh/crore amounts. |

All 130 original agent tests still pass (168 with the new ones).

## Not changed

- `backend-app` and `frontend-app`. The UI still brands itself "RBI/SEBI Copilot"
  (`frontend-app/src/app/layout.tsx`, `components/chat/*.tsx`) and the backend's API title
  says "RBI/SEBI Copilot API". Renaming is cosmetic and was left out to keep their Playwright
  and unit tests untouched.
