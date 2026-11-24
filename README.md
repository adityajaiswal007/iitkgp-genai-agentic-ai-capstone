# Generative AI and Agentic AI Capstone

Capstone project for the **Executive Post Graduate Programme in Generative AI and Agentic AI, IIT Kharagpur**.

## Project overview

RegAssist answers read-only questions about RBI and SEBI regulations using cited passages.
The repository includes a synthetic retrieval API for local development and an HTTP adapter
for connecting an external RBI/SEBI corpus.

## Applications and query flow

`frontend-app` (Next.js, port 3000) → `backend-app` (NestJS, port 3001) →
`agent-orchestrator` (FastAPI, port 8000) → a retrieval API (local mock on port 8001)
and the hosted Qwen model. PostgreSQL on port 5433 stores the UI conversations and attachments;
the agent owns its reasoning state in SQLite using the same conversation ID. Each query also
carries the complete committed PostgreSQL transcript. The agent uses that history to recover prior
context when its checkpoint or compact summary is missing.
Context exceeding the API/model budget produces an explicit error/limitation rather than a
silent history cut.

The backend calls `POST /v1/executions` and saves its `answer_text`, including citations.
There is no sample-answer fallback when the agent is unavailable. HTTP 502/503 reaches the UI.
The backend response mode `agent` identifies the service boundary; it does not assert Qwen
connectivity. If the agent uses offline `demo` inference, its answer explicitly says so.
Attachments remain downloadable and are forwarded for text extraction. RBI and SEBI file excerpts
can support cited answers and are scoped to the conversation.

## First-time setup

Use Node.js 24, Python 3.12, and running Docker Desktop for PostgreSQL.
Run these from the repository root:

```bash
(cd backend-app && npm ci)
(cd frontend-app && npm ci)
(cd agent-orchestrator && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]')
```

Create missing configuration files; preserve existing credentials:

```bash
(cd backend-app && { test -f .env || cp .env.example .env; })
(cd agent-orchestrator && { test -f .env || cp .env.example .env; })
```

In `backend-app/.env`, set `DB_PASSWORD` before initializing a fresh database. An existing
volume requires its existing password. The backend's `AGENT_BASE_URL` defaults to
`http://127.0.0.1:8000`, with a 130-second deadline (longer than the agent's 120-second budget).

Put the **Qwen API key only in `agent-orchestrator/.env`**:

```dotenv
AGENT_MODE=qwen
QWEN_BASE_URL=https://103.42.50.41:1033/v1
QWEN_API_KEY=your-real-api-key
QWEN_CA_CERT=./certificate/vm05-ca.crt
RAG_BASE_URL=http://127.0.0.1:8001
```

The provider base address is `https://103.42.50.41:1033`; the OpenAI-compatible client requires
`/v1` in its configured API base. The local certificate is
`agent-orchestrator/certificate/vm05-ca.crt`; the relative env path is resolved from that app's
working directory. Do not put Qwen credentials in frontend variables or the backend.
`AGENT_API_KEY`, if enabled, is a separate matching key in backend/agent env files;
`RAG_API_KEY`, if enabled, must match in agent/retrieval env files.

## Start all four applications

From the repository root, use a separate terminal for each command, in this order:

1. Mock document retrieval:
   ```bash
   cd agent-orchestrator
   .venv/bin/python -m uvicorn regulatory_agent.mock_retrieval:app --env-file .env --host 127.0.0.1 --port 8001
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
`http://127.0.0.1:8000/health`, and `http://127.0.0.1:8001/health`.
Agent health should show `mode: qwen` and `corpus: retrieval`. Health reports startup,
not successful model inference; try the RBI demo complaint question to verify generation against the mock.

The frontend defaults to `http://127.0.0.1:3001/api`. To override, set
`NEXT_PUBLIC_API_URL` in `frontend-app/.env.local` and restart/rebuild the frontend.

For agent + retrieval in Docker (after configuring the key and certificate):

```bash
cd agent-orchestrator
docker compose -f compose.yaml -f compose.retrieval.yaml -f compose.qwen.yaml up --build
```

The certificate override mounts the host file read-only at
`/agent-orchestrator/certificate/vm05-ca.crt` inside the agent container.
Use either local Python or Docker for each service to avoid port conflicts.
The backend Docker profile uses `host.docker.internal:8000`; override
`AGENT_DOCKER_BASE_URL` if the agent is on a different reachable host/network.

Concurrent retries can run inference more than once; PostgreSQL deduplicates saved turns,
not model calls. Deleting UI chats does not delete agent SQLite checkpoints. This is still
a shared local workspace without user authentication.

## Academic context

This repository documents coursework and capstone development for the programme.


# Agent + mock retrieval, using your Qwen configuration
(cd agent-orchestrator && docker compose \
  -f compose.yaml \
  -f compose.retrieval.yaml \
  -f compose.qwen.yaml \
  up -d --build)

# Frontend + backend + PostgreSQL + Redis
(cd frontend-app && docker compose --profile app up -d --build --wait)