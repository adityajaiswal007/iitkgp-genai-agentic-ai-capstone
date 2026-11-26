# Retrieval evaluation (bm25-baseline)

Index snapshot: `regassist-88d800ec1d0d`. Query = gold question text. Evidence hit = at least 60% of the gold evidence tokens appear in the fetched passages for the top-3 searched documents; oracle evidence uses the gold documents directly.

Skipped 22 refusal/unanswerable items; 0 answerable items cite documents that are not in the corpus.

| set | category | n | hit@1 | hit@3 | hit@5 | all_gold@5 | MRR | page_hit | page±1 | evidence_hit | oracle_evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RBI | ALL | 28 | 86% | 96% | 100% | 100% | 0.91 | 82% | 82% | 100% | 100% |
| RBI | comparison | 2 | 100% | 100% | 100% | 100% | 1.00 | 100% | 100% | 100% | 100% |
| RBI | lookup | 20 | 95% | 100% | 100% | 100% | 0.97 | 85% | 85% | 100% | 100% |
| RBI | table | 4 | 75% | 75% | 100% | 100% | 0.80 | 75% | 75% | 100% | 100% |
| RBI | version | 2 | 0% | 100% | 100% | 100% | 0.42 | 50% | 50% | 100% | 100% |
| TAX | ALL | 74 | 86% | 99% | 99% | 99% | 0.92 | 86% | 91% | 95% | 92% |
| TAX | act2025 | 28 | 86% | 100% | 100% | 100% | 0.93 | 68% | 79% | 93% | 93% |
| TAX | circular_notification | 15 | 73% | 93% | 93% | 93% | 0.82 | 93% | 93% | 100% | 93% |
| TAX | cross_doc | 5 | 80% | 100% | 100% | 100% | 0.90 | 100% | 100% | 80% | 60% |
| TAX | related_law | 12 | 100% | 100% | 100% | 100% | 1.00 | 100% | 100% | 92% | 92% |
| TAX | rules2026 | 14 | 93% | 100% | 100% | 100% | 0.96 | 100% | 100% | 100% | 100% |
