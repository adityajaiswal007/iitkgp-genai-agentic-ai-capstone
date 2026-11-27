# End-to-end evaluation (e2e-run1)

Run 2026-11-27 11:08, agent mode `qwen`, 124 questions, each in a fresh conversation. Answer correctness: LLM judge (same Qwen model) with deterministic fallback.

## Overview

| Set | Questions | Passed | Decision correct | Answer correct* | Partially correct* | Gold doc cited* | Gold page cited* |
|---|---|---|---|---|---|---|---|
| All | 124 | 19% | 37% | 12% | 10% | 97% | 80% |
| RBI | 30 | 33% | 57% | 33% | 10% | 94% | 94% |
| TAX | 94 | 15% | 31% | 4% | 9% | 100% | 67% |

\* Over questions expecting an answer. Answer correct and partially correct count an unanswered question as not correct; gold document and page cited are over the questions that were answered.

## Behaviour by expected decision

| Expected | n | answered | refused | clarify | abstained | error | correct decision |
|---|---|---|---|---|---|---|---|
| answer | 104 | 35 | 3 | 10 | 56 | 0 | 34% |
| refuse | 16 | 0 | 7 | 3 | 6 | 0 | 44% |
| clarify | 2 | 0 | 0 | 2 | 0 | 0 | 100% |
| abstain | 2 | 0 | 0 | 0 | 2 | 0 | 100% |

## By category

| Set | Category | n | Passed | Answer correct | Abstained |
|---|---|---|---|---|---|
| RBI | comparison | 2 | 0% | 0% | 50% |
| RBI | lookup | 22 | 41% | 41% | 32% |
| RBI | table | 4 | 25% | 25% | 25% |
| RBI | version | 2 | 0% | 0% | 50% |
| TAX | act2025 | 28 | 7% | 7% | 43% |
| TAX | circular_notification | 15 | 7% | 7% | 47% |
| TAX | cross_doc | 5 | 0% | 0% | 80% |
| TAX | refusal | 20 | 55% | n/a | 40% |
| TAX | related_law | 12 | 0% | 0% | 75% |
| TAX | rules2026 | 14 | 0% | 0% | 100% |

## Failure taxonomy (automatic first pass)

| Category | Count | Meaning |
|---|---|---|
| abstained_verification | 32 | Abstained although the evidence was retrieved (critic or checks rejected the draft) |
| abstained_system | 21 | Stopped by a system limit or error (tokens, deadline, model, context check) |
| incorrect_answer | 12 | Answered from the right document but the answer is wrong |
| unnecessary_clarification | 10 | Asked for clarification on a question it should have answered |
| incomplete_answer | 9 | Answered correctly but missed required facts |
| decision_mismatch | 9 | Declined, but in the wrong way (e.g. refused instead of clarifying) |
| abstained_retrieval_miss | 3 | Abstained because the right evidence was not retrieved |
| over_refusal | 3 | Refused a question it should have answered |
| wrong_source | 1 | Answered without citing the gold document |

Stop codes behind abstentions: INSUFFICIENT_EVIDENCE (41), MODEL_ATTEMPTS_EXHAUSTED (21), REPEATED_ACTION (2).

## Cost and latency

Latency per question: median 16.2 s, 90th percentile 24.6 s. Tokens per question: mean 12,630, total 1,566,069. Questions needing a revision: 46.

Review `graded.csv`: confirm or correct each automatic category in `manual_category`; subtler failures such as scope conflation need a human reading.
