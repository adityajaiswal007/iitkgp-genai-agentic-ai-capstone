# Generative AI and Agentic AI Capstone

Capstone project for the **Executive Post Graduate Programme in Generative AI and Agentic AI, IIT Kharagpur**.

## Project overview

RegAssist answers read-only questions about Indian financial regulation and income tax, using
cited passages from official documents. The corpus has 72 RBI Master Directions (commercial
banks and NBFCs) and 16 income-tax documents (the Income-tax Act, 2025, the Income-tax Rules,
2026, CBDT circulars and notifications, and related direct-tax Acts). SEBI is supported by the
agent but has no documents in the corpus yet.

## Applications and query flow

`frontend-app` (Next.js, port 3000) → `backend-app` (NestJS, port 3001) →
`agent-orchestrator` (FastAPI, port 8000) → `rag-service` (retrieval API, port 8001)
and the hosted Qwen model.

| Folder | Role |
| --- | --- |
| `frontend-app` | Chat interface |
| `backend-app` | Conversations, attachments, rate limits and daily token allowance; PostgreSQL on port 5433 and Redis on port 6380 |
| `agent-orchestrator` | LangGraph planner, executor and critic; calls retrieval and Qwen; keeps its reasoning state in SQLite under the same conversation ID |
| `rag-service` | Hybrid retrieval over the documents in `data/`; see [`rag-service/README.md`](rag-service/README.md) |
| `data` | RBI and income-tax documents and gold question sets; see [`data/README.md`](data/README.md) |
| `observability/langfuse` | Optional Langfuse trace dashboard on port 3100; see [`observability/langfuse/README.md`](observability/langfuse/README.md) |

The backend calls `POST /v1/executions` and saves its `answer_text`, including citations. There
is no sample-answer fallback when the agent is unavailable: HTTP 502/503 reaches the UI. Each
query carries the complete committed PostgreSQL transcript, which the agent uses to recover
context when its checkpoint or summary is missing. Context exceeding the model budget produces
an explicit error rather than a silent history cut. If the agent runs in offline `demo` mode,
its answers say so. Attachments remain downloadable and are forwarded for text extraction;
RBI, SEBI and income-tax file excerpts can support cited answers within their conversation.

Changes from the original `agent-orchestrator` branch are listed in
[`CHANGES_FROM_BASE.md`](CHANGES_FROM_BASE.md).

## First-time setup

Use Node.js 24, Python 3.12, and a running Docker Desktop for PostgreSQL and Redis. Run these
from the repository root:

```bash
(cd backend-app && npm ci)
(cd frontend-app && npm ci)
(cd agent-orchestrator && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]')
(cd rag-service && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test,ocr]')
```

Create the configuration files without overwriting any you already have:

```bash
(cd backend-app && { test -f .env || cp .env.example .env; })
(cd agent-orchestrator && { test -f .env || cp .env.example .env; })
(cd rag-service && { test -f .env || cp .env.example .env; })
```

## What to change in the `.env` files

The `.env` files hold passwords and keys, so they are never committed. Each app has its own.
Only the settings below need your attention; leave everything else at its default.

### `backend-app/.env`: required

| Setting | Change to |
| --- | --- |
| `DB_PASSWORD` | Any password you choose for the local PostgreSQL database. Set it **before** the first `npm run db:up`. If the database volume already exists, keep the password it was created with. |
| `AGENT_API_KEY` | Leave empty, unless you also set it in `agent-orchestrator/.env`; the two must match. |

The other values (ports, Redis, rate limits, `AGENT_BASE_URL=http://127.0.0.1:8000`, the
130-second agent timeout and the 100,000-token daily allowance) work as they are.

### `agent-orchestrator/.env`: required for real answers

| Setting | Change to |
| --- | --- |
| `QWEN_API_KEY` | Your Qwen API key. Put it **only** in this file, never in the backend or frontend. |
| `QWEN_CA_CERT` | The path to the Qwen server's CA certificate file, relative to `agent-orchestrator`. The certificate is not in the repository (`*.crt` files are ignored), so ask the team for it, save it as `agent-orchestrator/certificate/vm05-ca.crt`, and set `QWEN_CA_CERT=./certificate/vm05-ca.crt`. The example file's default name, `model.crt`, works too if you save it under that name. |
| `QWEN_BASE_URL` | Leave as `https://103.42.50.41:1033/v1` unless the team's model server moves. The `/v1` ending is required. |
| `AGENT_MODE` | Leave as `qwen`. Set `demo` only to test the plumbing offline; demo answers just repeat retrieved passages. |
| `RAG_BASE_URL` | Leave as `http://127.0.0.1:8001`, the retrieval service. |
| `AGENT_API_KEY`, `RAG_API_KEY` | Leave empty, or set each to match the backend (`AGENT_API_KEY`) and `rag-service/.env` (`RAG_API_KEY`). |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Leave the keys empty unless you run the Langfuse dashboard. `observability/langfuse/make_env.py` prints the values to paste here. |

Example of the lines you edit:

```dotenv
AGENT_MODE=qwen
QWEN_BASE_URL=https://103.42.50.41:1033/v1
QWEN_API_KEY=your-real-api-key
QWEN_CA_CERT=./certificate/vm05-ca.crt
RAG_BASE_URL=http://127.0.0.1:8001
```

### `rag-service/.env`: optional

With no changes, retrieval uses keyword search (BM25), which already works well. Fill in these
lines only to add the team's embedding server for hybrid retrieval:

| Setting | Change to |
| --- | --- |
| `EMBEDDING_BASE_URL` | The embedding server's address, ending in `/v1` |
| `EMBEDDING_MODEL` | The model name served there |
| `EMBEDDING_API_KEY` | Its key, if it needs one |
| `EMBEDDING_CA_CERT` | Path to its certificate, for example `./certificate/embedding.crt` (the `embedding.crt` from the `certificate` branch) |
| `RERANK_BASE_URL`, `RERANK_MODEL` | A reranking server, if the team has one |
| `RAG_API_KEY` | Leave empty, or match `agent-orchestrator/.env` |

Keep `RAG_DATA_DIR=../data` and `RAG_INDEX_DIR=index`. The first start after adding an
embedding model sends every passage to the server once, which can take several minutes; later
starts reuse the saved result. See [`rag-service/README.md`](rag-service/README.md) for details.

### `frontend-app/.env.local`: usually not needed

The frontend talks to `http://127.0.0.1:3001/api` by default. Only if the backend runs
elsewhere, create `frontend-app/.env.local` with `NEXT_PUBLIC_API_URL=<backend URL>/api` and
restart or rebuild the frontend.

## Start all four applications

From the repository root, use a separate terminal for each command, in this order:

1. Retrieval service (builds its index on first start, about 15 seconds):
   ```bash
   cd rag-service
   .venv/bin/python -m uvicorn regassist_rag.api:app --env-file .env --host 127.0.0.1 --port 8001
   ```
2. Agent orchestrator (after filling `QWEN_API_KEY`):
   ```bash
   cd agent-orchestrator
   .venv/bin/python -m uvicorn regulatory_agent.api:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
   ```
3. Backend and database:
   ```bash
   cd backend-app
   npm run db:up
   npm run db:migrate
   npm run start:dev
   ```
4. Frontend:
   ```bash
   cd frontend-app
   npm run dev
   ```

Open http://localhost:3000. Health endpoints: `http://127.0.0.1:3001/api/health`,
`http://127.0.0.1:8000/health` and `http://127.0.0.1:8001/health`. Agent health should show
`mode: qwen`; retrieval health shows the index snapshot and document counts (72 RBI, 16 TAX).
Health reports startup, not successful inference. To check the whole chain, ask "Within how
many days must a bank close a credit card after the cardholder asks?" (an RBI question) and
"What monetary limits does CBDT Circular 17/2019 set for departmental appeals?" (a tax
question). Both answers should cite their source documents.

The mock retrieval API used in the original branch still exists for agent tests
(`regulatory_agent.mock_retrieval`) but serves only fictional documents. Do not run it at the
same time as `rag-service`: both use port 8001.

## Run with Docker

Use Docker Compose 2.24 or newer. Configure the `.env` files and certificates first.

```bash
# Retrieval service on port 8001
(cd rag-service && docker compose up -d --build)

# Agent, pointed at the retrieval service on the host, using the Qwen certificate override
(cd agent-orchestrator && RAG_BASE_URL=http://host.docker.internal:8001 \
  docker compose -f compose.yaml -f compose.qwen.yaml up -d --build)

# Frontend + backend + PostgreSQL + Redis
(cd frontend-app && docker compose --profile app up -d --build --wait)
```

Do not add `compose.retrieval.yaml` to the agent command: it starts the mock retrieval API on
the same port. The Qwen override mounts the certificate named by `QWEN_CA_CERT` read-only
inside the agent container. The backend container reaches the agent at
`host.docker.internal:8000`; set `AGENT_DOCKER_BASE_URL` in `backend-app/.env` if the agent is
elsewhere. Use either local Python or Docker for each service, not both, to avoid port
conflicts.

## Trace dashboard (optional)

Langfuse shows every question as a trace: each planner, retrieval, Qwen and critic step
with its inputs, outputs, tokens and timing, and the exact reason when the agent
abstains. It runs in Docker on port 3100:

```powershell
cd observability\langfuse
python make_env.py        # once: creates .env and prints the agent's three LANGFUSE_ lines
docker compose up -d
```

Add the printed lines to `agent-orchestrator/.env`, restart the agent, and open
http://localhost:3100. Details are in
[`observability/langfuse/README.md`](observability/langfuse/README.md), including how to
publish showcase traces on Langfuse Cloud for the submission with
`agent-orchestrator/scripts/trace_showcase.py`.

## Tests and evaluation

```bash
(cd agent-orchestrator && .venv/bin/python -m pytest)
(cd rag-service && .venv/bin/pip install -e ../agent-orchestrator && .venv/bin/python -m pytest)
(cd rag-service && .venv/bin/python scripts/eval_retrieval.py --label my-run)
```

The retrieval evaluation runs every answerable gold question (30 RBI, 74 income tax) against
the retrieval service and writes `rag-service/eval_results/my-run/summary.md`.

### End-to-end evaluation

With the retrieval service and the agent running in `qwen` mode:

```powershell
cd agent-orchestrator
.venv\Scripts\python scripts\eval_e2e.py --label e2e-run1 --limit 5   # quick check
.venv\Scripts\python scripts\eval_e2e.py --label e2e-run1             # all 124 questions
```

Each gold question runs in a fresh conversation through the full agent. Answers are graded on
the decision (answer, refuse, clarify or abstain, as the gold set expects), correctness (an LLM
judge using the same Qwen endpoint, with deterministic key-fact and number checks as fallback),
citations (gold document and page) and forbidden content, and each failure is given a
failure-taxonomy category. Results go to `agent-orchestrator/eval_results/<label>/`:
`summary.md`, `failures.md`, `graded.csv` (with a `manual_category` column for review) and
`results.jsonl`. The run is resumable: rerun the same command after an interruption. With
Langfuse configured, scores are attached to each question's trace. Calls go straight to the
agent, so the backend's daily token allowance doesn't apply; expect 40–60 minutes and about
1.5 million Qwen tokens for a full run.

If the agent answers "I could not verify an answer", see why with (Windows paths shown):

```powershell
cd agent-orchestrator
.venv\Scripts\python scripts\debug_query.py "your question"
```

It prints the evidence the agent used, its draft claims, and the exact feedback from the
automatic checks and the critic.

## Limitations

Concurrent retries can run inference more than once; PostgreSQL deduplicates saved turns, not
model calls. Deleting UI chats does not delete agent SQLite checkpoints. This is a shared local
workspace without user authentication. The UI is still titled "RBI/SEBI Copilot".

## Academic context

This repository documents coursework and capstone development for the programme.
