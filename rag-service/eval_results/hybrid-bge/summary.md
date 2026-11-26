# Retrieval evaluation (hybrid-bge)

Index snapshot: `regassist-9c63d60f4cc0`. Query = gold question text. Evidence hit = at least 60% of the gold evidence tokens appear in the fetched passages for the top-3 searched documents; oracle evidence uses the gold documents directly.

Skipped 22 refusal/unanswerable items; 0 answerable items cite documents that are not in the corpus.

| set | category | n | hit@1 | hit@3 | hit@5 | all_gold@5 | MRR | page_hit | page±1 | evidence_hit | oracle_evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RBI | ALL | 28 | 89% | 96% | 96% | 96% | 0.92 | 89% | 89% | 100% | 100% |
| RBI | comparison | 2 | 100% | 100% | 100% | 100% | 1.00 | 100% | 100% | 100% | 100% |
| RBI | lookup | 20 | 95% | 100% | 100% | 100% | 0.97 | 95% | 95% | 100% | 100% |
| RBI | table | 4 | 75% | 75% | 75% | 75% | 0.75 | 75% | 75% | 100% | 100% |
| RBI | version | 2 | 50% | 100% | 100% | 100% | 0.67 | 50% | 50% | 100% | 100% |
| TAX | ALL | 74 | 89% | 99% | 99% | 99% | 0.94 | 85% | 91% | 92% | 91% |
| TAX | act2025 | 28 | 93% | 100% | 100% | 100% | 0.96 | 75% | 82% | 86% | 89% |
| TAX | circular_notification | 15 | 73% | 93% | 93% | 93% | 0.82 | 93% | 93% | 93% | 87% |
| TAX | cross_doc | 5 | 100% | 100% | 100% | 100% | 1.00 | 80% | 100% | 80% | 60% |
| TAX | related_law | 12 | 100% | 100% | 100% | 100% | 1.00 | 92% | 92% | 100% | 100% |
| TAX | rules2026 | 14 | 86% | 100% | 100% | 100% | 0.93 | 93% | 100% | 100% | 100% |
