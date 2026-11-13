# Regulatory Agent — Python execution API and notebook

Python 3.11+ project with a FastAPI service for the Qwen3 planner → executor → critic workflow.
The notebook remains a standalone teaching/demo artifact. The API imports normal Python modules;
it never executes notebook cells.

Scope includes AVL internal governance, regional leadership assignments, and compliance officer
rosters alongside regulatory documents. These read-only questions do not need an explicit AVL
prefix or financial year: the planner searches the AVL corpus before deciding evidence is absent.
During revision, status is reused only for unchanged document IDs and corpus snapshots so the
final critic still fits the ten-step limit. The local Qwen configuration allows 30 seconds per
model/tool attempt within the existing 120-second query deadline; output remains capped at 900
(the hosted endpoint accepts at most 1,024 output tokens).

**The supplied `.env.example` enables Qwen with local retrieval.** Without environment settings,
the Python default remains offline `demo` with a synthetic corpus. Set `RAG_BASE_URL` to use the
Track B retrieval service and its AVL policy corpus. Demo inference is a deterministic test double;
use Qwen mode for generated planner/executor/critic answers. A demo is not actual regulatory guidance.

## Conversation context

`POST /v1/executions` accepts optional `history`, an ordered array of
`{ "role": "user" | "assistant", "content": "..." }` messages preceding the current question.
The backend supplies the complete committed PostgreSQL transcript on every turn, so older
chats and lost/legacy agent checkpoints recover their context. Only user/assistant roles are
accepted; no caller-supplied system instructions are allowed.

The planner receives these as actual chat messages rather than a nested JSON history blob.
For fresh uploads, drafting and review use the resolved question, current filenames and fresh
evidence without prior assistant answers, which prevents old file values from anchoring the new
answer. Follow-ups without new files continue to receive the ordered conversation context. Standalone API clients can omit history and reuse
`conversation_id`; SQLite retains the complete compact transcript, including supplied history,
across restarts. New answers are stored as valid JSON without retrieved passage bodies. The old
six-turn, 1,500-character question and 2,000-character serialized-answer cuts are removed.

History resolves people, topics, and pronouns; it is not trusted evidence for new facts.
Before scope checking, a dedicated context step binds plural references to named people/entities
from earlier assistant messages. Those bindings must actually occur in the transcript. The
original latest request is preserved, and its attribute plus bound entities becomes the search
query; this prevents a rewrite from losing "email" or narrowing a plural request to one person.
Every new question retrieves and checks evidence again. The context step is charged to the
existing model/token budget and ten-step limit; a normal follow-up uses seven attempts. The existing 64-KiB request limit,
200-message API limit and model token budget still apply. Oversized backend transcripts receive
an explicit context-limit error rather than being silently truncated. A model token-budget
stop also reports a limitation; conversation storage in PostgreSQL is retained.

## Start with Docker

From this directory:

```sh
cp .env.example .env
# Fill QWEN_API_KEY first. This starts retrieval and mounts the configured CA.
docker compose -f compose.yaml -f compose.retrieval.yaml -f compose.qwen.yaml up --build
```

- Swagger UI: http://localhost:8000/docs
- OpenAPI contract: http://localhost:8000/openapi.json
- Health: http://localhost:8000/health

Compose binds the API to your machine's loopback interface. SQLite state is saved in the
`agent-state` named volume and survives container recreation. `docker compose down` retains it;
`docker compose down -v` deletes it. The image runs as a non-root user. Its default target is
now **the API on port 8000**, rather than Jupyter on 8888. Offline tests run during image builds.

Without Compose:

```sh
docker build -t regulatory-agent:local .
docker run --rm --name regulatory-agent -p 127.0.0.1:8000:8000 \
  --mount type=volume,src=regulatory-agent-data,dst=/data \
  --env-file .env -e AGENT_DATABASE_PATH=/data/conversations.sqlite regulatory-agent:local
```

The manual command overrides the local database path so SQLite lives in the mounted volume.
Compose handles this path automatically.

## Execute a query

```sh
curl -sS http://localhost:8000/v1/executions \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "customer_001",
    "question": "What is the RBI demo complaint process?",
    "as_of": "2026-01-01",
    "include_trace": true
  }'
```

The response includes:

```json
{
  "execution_id": "generated-uuid",
  "conversation_id": "customer_001",
  "status": "answered",
  "mode": "demo",
  "corpus": "synthetic",
  "answer": {
    "claims": [{"text": "In this fictional demo, ...", "citations": ["demo-rbi:p1"]}],
    "limitations": ["Fictional demonstration corpus; not actual regulatory guidance."],
    "message": "",
    "sources": []
  },
  "answer_text": "Rendered answer with citations and limitations",
  "metrics": {"steps": 6, "tokens_charged": 300, "revisions": 0, "latency_ms": 12.0},
  "trace": []
}
```

This is an abbreviated shape: actual `sources` contains the cited passage metadata, and trace
contains events when requested. Demo token usage comes from the test double, not a real model.

Follow up using the same conversation ID, including after restarting the service:

```sh
curl -sS http://localhost:8000/v1/executions \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id":"customer_001","question":"What is the complaint reference follow-up?"}'
```

If `conversation_id` is omitted, the API generates and returns one. Store it and send it on the
next turn. Supported inputs are `question`, `conversation_id`, `as_of` (YYYY-MM-DD),
`fiscal_year` (e.g. 2025-26), and `include_trace` (boolean, default false).
Only released answers and metadata are returned; internal plans, rejected drafts and conversation
history are not returned. `answer_text` is convenient for a backend/frontend chat message.

## Connect to your Qwen server with its CA certificate

Your curl command maps to these three connection settings in `.env`:

```dotenv
AGENT_MODE=qwen
QWEN_BASE_URL=https://103.42.50.41:1033/v1
QWEN_API_KEY=your-real-api-key
QWEN_CA_CERT=./certificate/vm05-ca.crt
```

The model defaults to `Qwen/Qwen3-8B`; `QWEN_MODEL` is an optional override. Use the URL ending
in `/v1`, not `/v1/chat/completions`: the SDK adds the chat-completions route. `QWEN_CA_CERT` is
the trusted **CA certificate/bundle** corresponding to curl's `--cacert`, not a client private key.
TLS certificate and hostname verification remain enabled. An invalid or missing configured CA
file fails at startup; there is no insecure fallback.

For a fresh configuration, copy `.env.qwen.example` to `.env` and fill `QWEN_API_KEY`.
Keep an existing `.env` and edit these fields if it already contains your settings.

Local Python uses the certificate path directly:

```sh
python -m uvicorn regulatory_agent.api:app --env-file .env --host 127.0.0.1 --port 8000
```

For Docker, add the certificate-mount override:

```sh
docker compose -f compose.yaml -f compose.qwen.yaml up -d --build
```

The override mounts the host certificate read-only at `/agent-orchestrator/certificate/vm05-ca.crt` and configures that
container path automatically. In `.env`, supply `./certificate/vm05-ca.crt` or an absolute host path;
local Python also expands home/environment variables. The host file must exist. The certificate
and API key are not copied into the image. This override enables Qwen mode; the ordinary Compose
command remains suitable for offline demos without a certificate.

Python callers can also configure the adapter explicitly:

```python
from regulatory_agent.models import QwenModel

model = QwenModel(
    base_url="https://103.42.50.41:1033/v1",
    api_key=your_api_key,
    ca_cert="/path/to/vm05-ca.crt",
)
# Use model with RegulatoryAgent; close model.client when finished.
```

There is no API key in the repository, and live connectivity to your server is not assumed by
the offline tests. A local HTTPS test server verifies custom-CA trust and rejection of untrusted
certificates. Other application limits and synthetic-corpus behavior remain unchanged.

## Use your Qwen3 endpoint

Edit `.env` before starting or restarting:

```dotenv
AGENT_MODE=qwen
QWEN_BASE_URL=https://your-provider.example/v1
QWEN_MODEL=your-exact-qwen3-model-id
QWEN_API_KEY=your-real-key
QWEN_JSON_MODE=none
QWEN_DISABLE_THINKING=0
AGENT_API_KEY=choose-a-private-service-key
```

`QWEN_API_KEY` authenticates the inference provider; `AGENT_API_KEY` protects this execution API.
When the latter is set, add `-H "X-API-Key: $AGENT_API_KEY"` to curl requests after exporting it
in your terminal, or enter the key via Swagger's **Authorize** control. Compose reads `.env` but
does not export its values into your terminal automatically. Never commit real keys.

The adapter uses an OpenAI-compatible chat-completions endpoint. Confirm the selected model is
≤8B for Track B. Configure `QWEN_JSON_MODE=json_object` or `json_schema` only if your endpoint
supports it. `QWEN_DISABLE_THINKING=1` passes the vLLM Qwen3 chat-template switch; use it only
when supported. Otherwise configure thinking mode according to your provider. Qwen3 thinking
can consume a small output budget before producing JSON; tune `AGENT_MAX_OUTPUT_TOKENS` and the
overall token budget together. Unsupported capability settings produce a controlled failure;
there are no hidden SDK retries or automatic fallback to demo mode.

For a Qwen server running on your Mac, use `http://host.docker.internal:PORT/v1` from Docker.
The adapter explicitly allows that local bridge. Hosted remote endpoints require HTTPS.
Credentials stay server-side and are not placed in the notebook, prompts, or traces.
The `/health` endpoint reports local startup, not successful model connectivity.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m uvicorn regulatory_agent.api:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
```

Use `pip install -r requirements.txt` for API runtime dependencies only. API settings and inference
credentials are loaded from the process environment. Uvicorn's `--env-file` loads `.env`; starting
the app without it does not implicitly read a file.

## Tests

```sh
python -m pytest -q
python validate_notebook.py
```

The package includes 28 orchestration regression tests plus API integration tests covering
conversation persistence, schema validation, optional authentication, bounded request sizes,
busy responses, sanitized errors, and the real Qwen SDK against a local mock HTTP endpoint.
Tests never require real provider credentials or external inference calls. The notebook retains
its separate 28-test offline suite. A live Qwen endpoint and real corpus still need evaluation.

Run the package tests in Docker:

```sh
docker build --target test -t regulatory-agent:test .
docker run --rm regulatory-agent:test python -m pytest -q
```

## Notebook in Docker

```sh
docker build --target notebook -t regulatory-agent:notebook .
docker run --rm -p 127.0.0.1:8888:8888 \
  --mount type=volume,src=regulatory-agent-notebooks,dst=/workspace \
  regulatory-agent:notebook
```

Open the Jupyter token URL from container logs and run the notebook. The volume preserves edited
notebooks and their local state; it is populated from the image on first use only. Use a new
volume name if you want a fresh copy after rebuilding. Live notebook calls remain explicit opt-in
via `RUN_LIVE=True` in section 9.

## API behavior and limits

- `POST /v1/executions` synchronously executes one turn. `answered`, `clarify`, `refused`, and
  `abstained` are valid workflow outcomes returned with HTTP 200. Inspect `status`, not just HTTP status.
- Validation errors return 422; oversized bodies return 413; missing/wrong configured API keys return
  401; an already running execution returns 409. Unexpected internal failures return sanitized 500.
  Errors use `{"error":{"code":"...","message":"..."}}`.
- Run **one worker and one service replica** with this SQLite implementation. Only one execution
  runs at a time; a second concurrent request receives `SERVICE_BUSY` rather than waiting indefinitely.
  This is an execution-capacity guard, not a per-user rate limiter. Add external rate limiting for deployment.
- Repeating a POST creates another turn and may incur another model charge. `execution_id` is a result
  identifier, not an idempotency key. Do not automatically retry after an ambiguous timeout.
- Per turn: at most 10 model/tool attempts, one revision, one retry/JSON repair per operation,
  a deadline, and a token allowance. Initial planner + three tools + draft + critic costs six attempts.
  A targeted one-tool revision costs four more. Larger replans stop safely at the cap.
- Token reservations use conservative serialized-input byte allowances plus output caps; they are not
  an exact Qwen tokenizer. Provider-reported usage replaces reservations; failed/no-usage calls retain
  the allowance. Shared team quotas need a durable atomic ledger and provider-side quota enforcement.
- A timed-out Python read-only tool thread can finish in the background. Real network/database
  adapters must implement native timeouts. Never reuse this wrapper for consequential write actions.

## Project layout and team integration

```text
src/regulatory_agent/
  api.py          # FastAPI request/response contracts and application lifecycle
  config.py       # Environment settings and validated execution limits
  contracts.py    # Typed plans, claims, tools, state and control primitives
  agent.py        # LangGraph planner/executor/critic and SQLite persistence
  models.py       # Qwen3 HTTP client, role prompts, offline test doubles
  tools.py        # Demo corpus plus HTTP search, passage-fetch and status adapters
tests/
  test_agent.py
  test_api.py
```

Set `RAG_BASE_URL` to connect `HttpCorpusTools` to the team's hybrid retrieval/reranking,
passage store and version catalog. Fixture search remains lexical only when no URL is configured. Status checks reflect
catalogued dates/year, not an independent check of current law. Entity-specific applicability,
assessment-year mapping, amendment completeness and real corpus sourcing remain domain/RAG work.
Forward metadata events to your team's Langfuse/LangSmith/OpenTelemetry dashboard as needed.
The NestJS backend is not automatically rewired: it can POST here with its persisted conversation
ID and use `answer_text` as the released response. Keep this service's key in backend configuration.

The service is read-only. It does not file returns, submit complaints or move money. Same-model
critic approval and valid citations are not proof of semantic correctness: evaluate against gold
answers and prompt-injection cases. The default API key is unset for local demos. A shared service
key is service authentication, **not customer-level ownership**; enforce conversation authorization
in the calling backend before routing customer requests. Do not expose this directly as a public
multi-tenant customer API.

SQLite checkpoints contain input, evidence and internal drafts in local plaintext. Use synthetic
or de-identified input here and add appropriate storage, retention and access controls before real
customer use. New turns reset evidence/counters and retain the last six conversation turns. Older
checkpoints may remain in SQLite. Automatic crash replay of in-flight paid calls is not implemented.

## References

- [Qwen serving and thinking configuration](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [FastAPI application lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)


## Connect the Track B retrieval service

The sibling `../document-retrieval` project implements all three tool contracts. Start it on
port 8001, then configure this service with:

```dotenv
RAG_BASE_URL=http://127.0.0.1:8001
RAG_API_KEY=matching-retrieval-service-key-if-set
```

For Docker, run `docker compose -f compose.yaml -f compose.retrieval.yaml up --build`.
Add `-f compose.qwen.yaml` with your existing Qwen configuration for generated answers.
The retrieval override uses `http://retrieval:8001` inside Docker and waits for its health check.
First startup downloads the CPU embedding and cross-encoder models; persistent weights and vector
cache live in the `retrieval-data` volume. See the retrieval README for hosted embeddings configuration.

`HttpCorpusTools` maps native HTTP timeouts, transport failures and 429/5xx responses to the existing
orchestrator retry path, maps missing documents to `NOT_FOUND`, and leaves response-schema validation
to `tool_attempt`. The adapter does not add retries. `fetch_passages` now accepts an optional `query`;
the executor supplies it so selected evidence matches the search. All other tool names/response shapes
are unchanged. `AVL` is an additional authority, without relabeling enterprise policies as RBI/SEBI/Tax.
Execution and health responses report `corpus="retrieval"` when the remote service is configured.
The standalone notebook retains its synthetic teaching fixtures.

## Chat attachment evidence

The backend sends the current files and earlier files from the same conversation to
`POST /v1/executions/attachments`. It extends the execution JSON with `attachments`, each
containing `name`, `mime_type`, and `content_base64`. The optional `current` flag distinguishes
new files from archived files when several documents are available. The ordinary execution endpoint retains
its 64-KiB limit; the file endpoint permits a bounded 36-MiB envelope with at most 64 KiB of
conversation metadata. Raw files are limited to 5 MB each, 25 MB total, and 20 unique files per
conversation request. These limits fail explicitly; bytes are not silently dropped.

The agent extracts TXT/MD/CSV/JSON, PDF, DOCX paragraphs/tables, and XLSX cached cell values.
Images and scanned PDF pages use Tesseract OCR (the first frame for animated images), not visual
reasoning. Formula execution, macros, embedded instructions and external links are not run.
PDF pages and spreadsheet sheet names are retained in source metadata. Unsupported, unreadable,
encrypted, overlarge or over-budget extraction produces an explicit file error.

Parsing runs in separate processes with wall-clock bounds and Linux CPU/memory limits.
Extraction workers receive a minimal environment without model/API credentials. Office
archive expansion and image dimensions are bounded. Each file may contain up to 300,000 extracted
characters (500,000 combined) and PDFs up to 100 pages; the whole extraction phase is limited to 30 seconds and is
part of the query deadline. OCR accuracy depends on the input quality.

File passages use authority `UPLOAD`, deterministic content hashes and page/chunk citations.
A conversation-scoped lexical search selects relevant excerpts (at most two chunks per selected
file) for the same executor and critic checks as indexed evidence. Answers include attachment
filenames. This is excerpt retrieval, not a guarantee that every part of a long file was read.
Extracted passages survive in the conversation checkpoint; the backend also re-sends archived
PostgreSQL files so old chats recover even if that checkpoint is lost. Uploaded files are not
added to the shared organizational corpus and are not available to other conversation IDs.

Docker installs OCR automatically. Local development requires the updated Python dependencies
and a `tesseract` executable on PATH (for example, `brew install tesseract` on macOS).

For a corporate HTTPS proxy, an optional trusted public CA bundle can be supplied only for
package installation, without disabling TLS or copying it into the runtime image:

```bash
docker build --secret id=build_ca_bundle,src=/absolute/path/to/trusted-ca-bundle.pem -t regulatory-agent:api .
```
