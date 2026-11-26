# RegAssist retrieval service

Hybrid retrieval over the RBI Directions and the income-tax corpus. It replaces the mock
retrieval API: it serves the same three tool endpoints on the same port (8001), so the
agent orchestrator connects to it without code changes.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/tools/search_documents` | Rank documents for one authority (`RBI`, `TAX`, `SEBI`) |
| `POST /v1/tools/fetch_passages` | Return cited passages (page, paragraph/section, URL) from chosen documents |
| `POST /v1/tools/lookup_document_status` | Say whether each document is in force on a date, from the catalog |
| `GET /health` | Snapshot ID, document counts, whether dense retrieval and reranking are on |
| `GET /v1/catalog` | Every indexed document with its dates and status metadata |
| `POST /v1/search_chunks` | Chunk-level results with text, for evaluation and debugging |

The three tool responses contain exactly the fields of the agent's `SearchData`,
`FetchData` and `StatusData` schemas, which the agent validates strictly.

## Corpus

| Authority | Documents | Source |
| --- | --- | --- |
| RBI | 72 Master Directions (37 commercial bank, 35 NBFC) | `../data/RBI` with `manifest_frozen.csv` |
| TAX | 16 in-scope documents: the Income-tax Act, 2025 and Income-tax Rules, 2026, 10 CBDT circulars and notifications, and 4 related direct-tax Acts | `../data/Tax` with `tax_catalog.csv` |

The 19 out-of-scope Acts in the tax folder are not indexed unless
`RAG_INCLUDE_OUT_OF_SCOPE=1`. The duplicate Black Money Act file is never indexed; gold-set
references to it resolve to the original.

The Income-tax Rules, 1962 are not in the corpus, and their gold questions were removed
(see `../data/README.md`). Other file names for a document, such as those used in the gold
sets, are listed in the `aliases` column of `tax_catalog.csv`.

## How retrieval works

1. **Extraction.** `pypdfium2` reads each page. Running headers, page numbers,
   table-of-contents rows and the Acts' "arrangement of sections" pages are removed. Pages without a text layer go through Tesseract OCR
   when it is installed (the Docker image includes it).
2. **Chunking.** Chunks start at RBI paragraph numbers ("19."), Act section numbers
   ("2. Definitions.—") or rule numbers, target about 1,200 characters (1,500 maximum), and
   never cross a page, so every citation has a single page. Each chunk after the first on a
   page begins with the last ~250 characters of the previous one, so a sentence or table row
   cut at a boundary appears whole in at least one chunk. Chunks stay under 1,800 characters
   because the agent shows the model, and number-checks, only the first 1,800 characters of
   each passage; the service refuses settings that would exceed this. Numbers must increase,
   so numbered list items and table rows inside a paragraph do not start a new one.
   Annexes, schedules, appendices and forms restart the numbering and are labelled as items.
   Each chunk carries the nearest heading and a label such as `paras 15–17`,
   `section 2 (contd.)`, `rule 162` or `item 4`. Indian amounts whose separators were scanned
   as dots ("1,00.00.000") are repaired, and the ₹ sign is restored where a PDF font extracts
   it as a backtick.
3. **Ranking.** BM25 (with English stemming) over title + heading + chunk text. If an
   embedding server is configured, dense cosine similarity is fused with BM25 by reciprocal
   rank fusion. If a reranker is configured, it reorders the top 30 chunks; if it fails, the
   fused order is used.
4. **Document scores** come from each document's best chunks plus a title match. Two
   query signals adjust them: an instrument identifier in the question ("Notification
   41/2022", "Act 22 of 2015") strongly favours the document with that identifier, and for RBI
   a question about NBFCs or about banks favours the matching set of Directions.
5. **Passages.** Each requested document gets its best passage first, then remaining slots
   (8 by default, 10 maximum) go by relevance.
6. **Status.** Effective dates come only from the catalog. `../data/RBI/catalog_overrides.csv`
   corrects the frozen manifest using each Direction's own commencement clause. For example,
   files 01, 02, 03 and 04 take effect on 1 April 2027, and file 15 (IRACP Directions, 2025)
   stops applying on that date because file 04 repeals it. Catalog notes, such as tax
   circulars having been issued under the repealed 1961 Act, are passed to the agent in the
   status reason.

The index is identified by a snapshot ID (a hash of document hashes and chunking settings).
It rebuilds automatically when that changes and is reused otherwise. The snapshot ID is
returned with every passage as `corpus_snapshot`.

## Run on the host

Python 3.11 or newer.

```bash
cd rag-service
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test,ocr]'
cp .env.example .env            # optional: embedding/rerank settings
.venv/bin/python -m regassist_rag.build_index          # optional; the API builds on first start
.venv/bin/python -m uvicorn regassist_rag.api:app --env-file .env --host 127.0.0.1 --port 8001
```

Building the BM25 index takes about 15 seconds. Then start the agent exactly as the root
README describes, but skip the mock retrieval step: `agent-orchestrator/.env` already points
`RAG_BASE_URL` at `http://127.0.0.1:8001`.

## Run with Docker

```bash
cd rag-service
docker compose up -d --build
```

For the agent in Docker, start it **without** `compose.retrieval.yaml` (that file starts the
mock on the same port) and point it at this service:

```bash
cd agent-orchestrator
RAG_BASE_URL=http://host.docker.internal:8001 docker compose -f compose.yaml -f compose.qwen.yaml up -d --build
```

## Dense retrieval and reranking

Set these in `.env` (see `.env.example`):

```dotenv
EMBEDDING_BASE_URL=https://your-embedding-host:port/v1
EMBEDDING_MODEL=your-model-name
EMBEDDING_API_KEY=...
EMBEDDING_CA_CERT=./certificate/embedding.crt
```

The `certificate` branch's `embedding.crt` is a Caddy local root CA, which suggests the
team's embedding server sits behind Caddy; copy it to `rag-service/certificate/` and use the
path above. The whole corpus (about 12,200 chunks) is embedded once and cached in the index
folder; it is re-embedded only when the model or documents change. Models that expect
instruction prefixes (for example e5's `query: ` and `passage: `) can use
`EMBEDDING_QUERY_PREFIX` and `EMBEDDING_DOCUMENT_PREFIX`.

A reranker is any endpoint accepting `POST {base}/rerank` with `query`, `documents` and
`top_n`, returning `results` with `index` and `relevance_score`. vLLM, Text Embeddings
Inference, Infinity and Jina-compatible servers do this.

## Evaluation

```bash
.venv/bin/python scripts/eval_retrieval.py --label bm25-baseline
.venv/bin/python scripts/eval_retrieval.py --base-url http://127.0.0.1:8001 --label hybrid
```

The script runs every answerable gold question from both sets with the question as the query
and writes `eval_results/<label>/summary.md` and `per_question.csv`. Metrics: whether a gold
document is ranked 1st, in the top 3 or top 5; mean reciprocal rank; whether a fetched
passage is on a gold page; and whether the fetched passages contain the gold evidence text.

BM25 with overlapping chunks (`eval_results/bm25-overlap`, the current configuration):

| Set | Questions | Gold doc 1st | In top 3 | Gold page fetched (±1) | Evidence fetched |
| --- | --- | --- | --- | --- | --- |
| RBI | 28 | 86% | 96% | 82% | 96% |
| TAX | 74 | 89% | 99% | 92% | 95% |

`eval_results/bm25-baseline` is the earlier run with longer chunks and no overlap. Its
evidence figures are slightly optimistic: some chunks were longer than the 1,800 characters
the agent actually reads.

The right document is almost always found. The weaker figure is the gold page for questions
on the Income-tax Act, 2025 (79% within one page): with 686 pages and long sections such as
the definitions in section 2, choosing the right passage is harder. That is what dense
retrieval and reranking should improve. Run the hybrid configuration with the same script to
measure it.

## Tests

```bash
.venv/bin/pip install -e ../agent-orchestrator   # enables the agent-compatibility tests
.venv/bin/python -m pytest
```

The tests build a small index from real corpus files. `test_agent_compatibility.py` runs the
agent's own `HttpCorpusTools` adapter and schemas against this service. Dense and rerank
paths are tested against fake servers.

## Agent support for tax documents

The agent orchestrator accepts the `TAX` authority (see `../CHANGES_FROM_BASE.md`): its
planner routes income-tax questions to `TAX`, and `test_agent_compatibility.py` runs the
agent's adapter against this service for both RBI and TAX. `BOTH` still means RBI and SEBI
together; a question spanning RBI and income tax is answered from the authority the planner
judges primary.

SEBI is accepted for contract compatibility but returns no hits, because there are no SEBI
documents in the corpus.
