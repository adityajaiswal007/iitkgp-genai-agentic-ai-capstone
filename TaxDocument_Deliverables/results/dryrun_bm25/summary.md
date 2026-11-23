# Strategy comparison  (context=bm25, split=all, n=100 per strategy)  **[DRY-RUN – synthetic mock model, not real results]**

| Strategy | Strict acc | Lenient acc | Decision acc | Schema valid | Citation grounded | Over-refusal | Under-refusal | Calls/q | Mean latency s |
|---|---|---|---|---|---|---|---|---|---|
| s1 | 53.0% | 77.0% | 87.0% | 97.0% | 58.2% | 12.5% | 5.0% | 1.1 | 0.0 |

## Accuracy by category (strict)

| category | n | s1 |
|---|---|---|
| act2025 | 28 | 39.3% |
| circular_notification | 15 | 60.0% |
| cross_doc | 5 | 20.0% |
| refusal | 20 | 85.0% |
| related_law | 12 | 16.7% |
| rules1962 | 6 | 66.7% |
| rules2026 | 14 | 64.3% |

## Failure taxonomy (count of questions)

| failure mode | s1 |
|---|---|
| hallucinated_citation | 9 |
| injection_followed | 1 |
| numeric_error | 5 |
| over_refusal | 10 |
| partial_answer | 4 |
| reasoning_error | 1 |
| retrieval_miss | 14 |
| schema_invalid | 3 |
