# Generative AI and Agentic AI Capstone

Capstone project for the **Executive Post Graduate Programme in Generative AI and Agentic AI, IIT Kharagpur**.

## Project overview

RegAssist answers read-only questions about RBI and SEBI regulations using cited passages.
The real retrieval service supplies RBI Master Directions for commercial banks and NBFCs.
The current corpus has no SEBI documents, so the agent reports that gap when asked for SEBI evidence.

## Applications and query flow

`frontend-app` (Next.js, port 3000) → `backend-app` (NestJS, port 3001) →
`agent-orchestrator` (FastAPI, port 8000) → `rag-service` (retrieval API, host port 8002)
and the hosted Qwen model.

| Folder | Role |
| --- | --- |
| `frontend-app` | Chat interface |
| `backend-app` | Conversations, attachments, rate limits and daily token allowance; PostgreSQL on port 5433 and Redis on port 6380 |
| `agent-orchestrator` | LangGraph planner, executor and critic; calls retrieval and Qwen; keeps its reasoning state in SQLite under the same conversation ID |
| `rag-service` | Hybrid retrieval over the documents in `data/`; see [`rag-service/README.md`](rag-service/README.md) |
| `data` | RBI and income-tax documents and gold question sets; see [`data/README.md`](data/README.md) |

The backend calls `POST /v1/executions` and saves its `answer_text`, including citations. There
is no sample-answer fallback when the agent is unavailable: HTTP 502/503 reaches the UI. Each
query carries the complete committed PostgreSQL transcript, which the agent uses to recover
context when its checkpoint or summary is missing. Context exceeding the model budget produces
an explicit error rather than a silent history cut. If the agent runs in offline `demo` mode,
its answers say so. Attachments remain downloadable and are forwarded for text extraction;
RBI and SEBI file excerpts can support cited answers within their conversation.

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
| `RAG_BASE_URL` | Leave as `http://127.0.0.1:8002`, the retrieval service. |
| `AGENT_API_KEY`, `RAG_API_KEY` | Leave empty, or set each to match the backend (`AGENT_API_KEY`) and `rag-service/.env` (`RAG_API_KEY`). |

Example of the lines you edit:

```dotenv
AGENT_MODE=qwen
QWEN_BASE_URL=https://103.42.50.41:1033/v1
QWEN_API_KEY=your-real-api-key
QWEN_CA_CERT=./certificate/vm05-ca.crt
RAG_BASE_URL=http://127.0.0.1:8002
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
   .venv/bin/python -m uvicorn regassist_rag.api:app --env-file .env --host 127.0.0.1 --port 8002
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

Open http://localhost:3000. Health endpoints are `http://127.0.0.1:3001/api/health`,
`http://127.0.0.1:8000/health` and `http://127.0.0.1:8002/health`. Retrieval health reports the
index snapshot and document counts. Ask an RBI credit-card closure question to verify that the
agent cites a real source. A SEBI-only question should report that no matching documents were found.

## Run with Docker

Use Docker Compose 2.24 or newer. Configure the environment files and Qwen certificate first.

```bash
# Agent and real retrieval
(cd agent-orchestrator && docker compose -f compose.yaml -f compose.rag.yaml \
  -f compose.qwen.yaml up -d --build --wait)

# Frontend + backend + PostgreSQL + Redis
(cd frontend-app && docker compose --profile app up -d --build --wait)
```

For a synthetic demo, use the mock override:

```bash
(cd agent-orchestrator && AGENT_MODE=demo docker compose -f compose.yaml \
  -f compose.retrieval.yaml up -d --build --wait)
```

Choose one retrieval override per agent run. The real service uses host port 8002, and the
mock uses host port 8001. A standalone RAG service can run alongside a mock demo. For separately
managed retrieval, configure
`RAG_BASE_URL` in the agent and start it with the base Compose file. Set the same `RAG_API_KEY`
in the agent and RAG environment files if authentication is enabled. The Qwen certificate override
mounts `QWEN_CA_CERT` read-only in the agent. The backend container reaches the agent at
`host.docker.internal:8000`. For a different address, set `AGENT_DOCKER_BASE_URL` in
`backend-app/.env`.

## Tests and evaluation

```bash
(cd agent-orchestrator && .venv/bin/python -m pytest)
(cd rag-service && .venv/bin/pip install -e ../agent-orchestrator && .venv/bin/python -m pytest)
(cd rag-service && .venv/bin/python scripts/eval_retrieval.py --label my-run)
```

The evaluation runs every answerable gold question (28 RBI, 74 income tax) against the
retrieval service and writes `rag-service/eval_results/my-run/summary.md`.

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


## TLDR

# 1. Real RAG service on port 8002
(cd rag-service && docker compose up -d --build --wait)

# 2. Agent orchestrator on port 8000, using real RAG and Qwen
(cd agent-orchestrator && \
  docker build --target api-qwen \
    --secret id=build_ca_bundle,src=../rag-service/certificate/build-ca.pem \
    -t regulatory-agent:api-qwen . && \
  RAG_BASE_URL=http://host.docker.internal:8002 \
    docker compose -f compose.yaml -f compose.qwen.yaml up -d --no-build --wait)

# 3. Frontend on port 3000 and backend API on port 3001
(cd frontend-app && docker compose --profile app up -d --build --wait)
