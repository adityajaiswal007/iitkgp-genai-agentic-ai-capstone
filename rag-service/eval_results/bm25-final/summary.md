# Retrieval evaluation (bm25-final)

Index snapshot: `regassist-b629e8374aca`. Query = gold question text. Evidence hit = at least 60% of the gold evidence tokens appear in the fetched passages for the top-3 searched documents; oracle evidence uses the gold documents directly.

Skipped 20 refusal/unanswerable items; 0 answerable items cite documents that are not in the corpus.

| set | category | n | hit@1 | hit@3 | hit@5 | all_gold@5 | MRR | page_hit | page±1 | evidence_hit | oracle_evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RBI | ALL | 30 | 87% | 97% | 100% | 100% | 0.92 | 80% | 80% | 97% | 100% |
| RBI | comparison | 2 | 100% | 100% | 100% | 100% | 1.00 | 100% | 100% | 100% | 100% |
| RBI | lookup | 22 | 95% | 100% | 100% | 100% | 0.98 | 86% | 86% | 100% | 100% |
| RBI | table | 4 | 75% | 75% | 100% | 100% | 0.80 | 75% | 75% | 75% | 100% |
| RBI | version | 2 | 0% | 100% | 100% | 100% | 0.42 | 0% | 0% | 100% | 100% |
| TAX | ALL | 74 | 89% | 99% | 100% | 100% | 0.94 | 85% | 91% | 95% | 89% |
| TAX | act2025 | 28 | 89% | 100% | 100% | 100% | 0.95 | 68% | 82% | 89% | 86% |
| TAX | circular_notification | 15 | 73% | 93% | 100% | 100% | 0.84 | 93% | 93% | 100% | 87% |
| TAX | cross_doc | 5 | 100% | 100% | 100% | 100% | 1.00 | 100% | 100% | 80% | 60% |
| TAX | related_law | 12 | 100% | 100% | 100% | 100% | 1.00 | 92% | 92% | 100% | 100% |
| TAX | rules2026 | 14 | 93% | 100% | 100% | 100% | 0.96 | 100% | 100% | 100% | 100% |
