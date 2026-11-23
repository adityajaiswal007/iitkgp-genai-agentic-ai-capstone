# Strategy comparison  (context=oracle, split=all, n=100 per strategy)  **[DRY-RUN – synthetic mock model, not real results]**

| Strategy | Strict acc | Lenient acc | Decision acc | Schema valid | Citation grounded | Over-refusal | Under-refusal | Calls/q | Mean latency s |
|---|---|---|---|---|---|---|---|---|---|
| s0 | 79.0% | 80.0% | 86.0% | 100.0% | n/a | 2.5% | 60.0% | 1.0 | 0.0 |
| s1 | 66.0% | 81.0% | 93.0% | 97.0% | 76.9% | 5.0% | 5.0% | 1.1 | 0.0 |
| s2 | 73.0% | 90.0% | 97.0% | 100.0% | 77.5% | 3.8% | 5.0% | 5.0 | 0.0 |
| s3 | 19.0% | 94.0% | 100.0% | 100.0% | 0.0% | 0.0% | 5.0% | 3.0 | 0.0 |

## Accuracy by category (strict)

| category | n | s0 | s1 | s2 | s3 |
|---|---|---|---|---|---|
| act2025 | 28 | 89.3% | 71.4% | 67.9% | 0.0% |
| circular_notification | 15 | 93.3% | 66.7% | 73.3% | 0.0% |
| cross_doc | 5 | 100.0% | 20.0% | 60.0% | 0.0% |
| refusal | 20 | 40.0% | 90.0% | 95.0% | 95.0% |
| related_law | 12 | 66.7% | 25.0% | 58.3% | 0.0% |
| rules1962 | 6 | 83.3% | 66.7% | 50.0% | 0.0% |
| rules2026 | 14 | 100.0% | 71.4% | 78.6% | 0.0% |

## Failure taxonomy (count of questions)

| failure mode | s0 | s1 | s2 | s3 |
|---|---|---|---|---|
| fabricated_on_false_premise | 1 | 0 | 0 | 0 |
| hallucinated_citation | 0 | 14 | 15 | 80 |
| injection_followed | 1 | 1 | 1 | 1 |
| numeric_error | 2 | 6 | 2 | 0 |
| over_refusal | 2 | 4 | 3 | 0 |
| partial_answer | 4 | 4 | 5 | 0 |
| reasoning_error | 1 | 2 | 1 | 0 |
| schema_invalid | 0 | 3 | 0 | 0 |
| under_refusal | 10 | 0 | 0 | 0 |
