# RegAssist

Capstone project for the **Executive Post Graduate Programme in Generative AI and Agentic AI, IIT Kharagpur**.

RegAssist answers questions about RBI and SEBI regulations using cited passages. It includes a mock retrieval service with fictional documents for local testing. Set `RAG_BASE_URL` to use an external corpus.

## Services

| Service | Local address | Role |
| --- | --- | --- |
| [Frontend](frontend-app/README.md) | `http://localhost:3000` | Next.js chat workspace |
| [Backend](backend-app/README.md) | `http://127.0.0.1:3001/api` | NestJS conversation, attachment, and usage API |
| [Agent orchestrator](agent-orchestrator/README.md) | `http://127.0.0.1:8000` | FastAPI execution service for retrieval and Qwen inference |
| Mock retrieval | `http://127.0.0.1:8001` | Synthetic RBI and SEBI documents |
| PostgreSQL | `127.0.0.1:5433` | Conversations, attachments, and daily token usage |
| Redis | `127.0.0.1:6380` | Browser request rate limits |

The backend sends the committed chat history and remaining daily token allowance with each turn. It uses `POST /v1/executions` for text-only requests and `POST /v1/executions/attachments` when the request includes current or archived files. The agent retrieves evidence, generates a cited answer, and keeps its checkpoints in SQLite under the same conversation ID. PostgreSQL stores the answer and citations that the user sees.

## Configure the services

Run these commands from the repository root to create configuration files if they are missing.

```bash
(cd backend-app && { test -f .env || cp .env.example .env; })
(cd agent-orchestrator && { test -f .env || cp .env.example .env; })
```

In `backend-app/.env`, set `DB_PASSWORD` before initializing PostgreSQL. Reuse the original password if you already have a database volume.

In `agent-orchestrator/.env`, set `QWEN_API_KEY` for hosted inference. Set `QWEN_BASE_URL` to your provider's OpenAI-compatible base URL ending in `/v1`. Set `QWEN_CA_CERT` to the provider’s private CA certificate path, or clear the example value if your provider uses a publicly trusted certificate. Relative paths resolve from `agent-orchestrator` for these commands. Keep `RAG_BASE_URL=http://127.0.0.1:8001` for host development with the mock.

If you set `AGENT_API_KEY`, use the same value in the backend and agent environment files. If you set `RAG_API_KEY`, use the same value for the agent and retrieval service. Store provider credentials in the agent environment only.

## Run with Docker Compose

Use Docker Compose 2.20.3 or newer. For Qwen with a private CA, set the key and certificate path above, then run both commands from the repository root.

```bash
(cd agent-orchestrator && docker compose -f compose.yaml -f compose.retrieval.yaml -f compose.qwen.yaml up -d --build)
(cd frontend-app && docker compose --profile app up -d --build --wait)
```

The Qwen override mounts the CA certificate read-only. The backend container reaches the agent at `host.docker.internal:8000` by default. Set `AGENT_DOCKER_BASE_URL` in the backend environment to reach another address.

For Qwen with a publicly trusted certificate, omit `-f compose.qwen.yaml` from the first command.

For an offline run, use this command in place of the first command above. Demo answers are labelled in the chat.

```bash
(cd agent-orchestrator && AGENT_MODE=demo docker compose -f compose.yaml -f compose.retrieval.yaml up -d --build)
```

If package downloads fail with a TLS certificate error, use the [build CA override](agent-orchestrator/README.md#build-behind-a-corporate-https-proxy).

## Run the apps on the host

Use Node.js 24.11 or newer in the Node 24 line, Python 3.12, and Docker for PostgreSQL and Redis. Install Tesseract for OCR on images and scanned PDFs.

```bash
(cd backend-app && npm ci)
(cd frontend-app && npm ci)
(cd agent-orchestrator && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]')
```

For offline inference on the host, set `AGENT_MODE=demo` in the agent’s `.env`. Run each block in a separate terminal from the repository root.

```bash
cd agent-orchestrator
.venv/bin/python -m uvicorn regulatory_agent.mock_retrieval:app --env-file .env --host 127.0.0.1 --port 8001
```

```bash
cd agent-orchestrator
.venv/bin/python -m uvicorn regulatory_agent.api:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
```

```bash
cd backend-app
npm run db:up
npm run db:migrate
npm run start:dev
```

```bash
cd frontend-app
npm run dev
```

## Try a query

Open [RegAssist](http://localhost:3000) and ask “What is the RBI demo complaint process?” The mock documents are fictional. Use sample content in this shared workspace, which has no user authentication.

Check the [backend](http://127.0.0.1:3001/api/health), [agent](http://127.0.0.1:8000/health), and [retrieval](http://127.0.0.1:8001/health) health endpoints to verify startup. A query checks inference and retrieval together. See each service README for tests, configuration, and limits.
