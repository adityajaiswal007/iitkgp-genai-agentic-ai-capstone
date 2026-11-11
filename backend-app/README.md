# Operations Copilot backend

NestJS 12 / TypeScript orchestrator API with PostgreSQL 17, Redis-backed browser rate limits, TypeORM migrations, Swagger, request validation, and a separate orchestration module. The Next.js frontend in `../frontend-app` now saves and retrieves conversations through this API.

## Start everything in Docker

Docker Desktop or Docker with Colima must be running. Node.js is not needed on the host for this option.

```bash
# First-time setup only, if .env does not exist:
cp .env.example .env
# Replace DB_PASSWORD in .env before starting the database.

docker compose --profile app up -d --build --wait
```

On the configured development machine, `.env` has already been created with a generated local password. Do not overwrite it after the database volume has been initialized: changing the environment variable does not change an existing PostgreSQL role's password.

- API: http://127.0.0.1:3001/api
- Interactive documentation: http://127.0.0.1:3001/api/docs
- OpenAPI JSON: http://127.0.0.1:3001/api/docs-json
- Readiness check: http://127.0.0.1:3001/api/health
- PostgreSQL from the host: `127.0.0.1:5433`
- PostgreSQL from the API container: `db:5432`
- Rate-limit Redis from the host: `127.0.0.1:6380` (configurable with `REDIS_PORT`)
- Rate-limit Redis from the API container: `redis:6379`

The API container runs migrations before starting. PostgreSQL, Redis, and the API have health checks. The API runs as the unprivileged `node` user; host-facing ports bind to loopback. Database data lives in `operations-copilot_postgres_data`; rate-limit state lives in `operations-copilot_rate_limit_redis_data`. Redis uses AOF persistence with one-second fsync and no eviction. API restarts retain quotas; an abrupt Redis crash can lose approximately the last second of writes. Container recreation and `docker compose down` preserve volumes. `docker compose down -v` deletes them.

On this Mac, Docker runs in a Colima profile named `capstone`:

```bash
colima start capstone
docker context use colima-capstone
```

The existing macOS-trusted corporate network CA was also added to this VM so Docker image downloads can use TLS verification on this network. No certificate-verification bypass is configured.

## Develop the API on the host

Use Node.js 24. Keep PostgreSQL and Redis in Docker. Stop the API container first if it is using port 3001. `npm run db:up` starts both dependencies.

```bash
nvm install
nvm use
npm ci
npm run db:up
npm run db:migrate
npm run start:dev
```

`npm run db:down` stops PostgreSQL without deleting its data. To run a compiled API locally: `npm run build` followed by `npm run start:prod`.

## Conversation API

All routes below are under `/api`.

| Method | Route | Behaviour |
| --- | --- | --- |
| POST | `/conversations` | Create `{ "title": "Vendor review" }`; omitted title defaults to `New chat` |
| GET | `/conversations` | List metadata; `page`, `pageSize` (1–100), and optional `search` |
| GET | `/conversations/:id` | Get metadata, timestamps, and current `version` |
| PATCH | `/conversations/:id` | Rename with `{ "title": "Q4 vendor review" }` |
| DELETE | `/conversations/:id` | Delete conversation and messages; returns 204 even when already deleted |
| GET | `/conversations/:id/messages` | Paginated messages, ordered by sequence ascending |
| POST | `/conversations/:id/messages` | Generate and atomically persist one user/assistant turn |
| GET | `/health` | PostgreSQL and Redis readiness and current orchestrator mode |

Conversation metadata:

```json
{
  "id": "a-server-generated-uuid",
  "title": "Vendor review",
  "version": 0,
  "createdAt": "2026-11-04T12:00:00.000Z",
  "updatedAt": "2026-11-04T12:00:00.000Z"
}
```

Send a message using the version from your latest conversation read. `requestId` must be a new UUID v4 for each distinct turn:

```json
{
  "content": "Help me plan a vendor review.",
  "expectedVersion": 0,
  "requestId": "927a9b9e-f656-45ca-b66c-179187951a83"
}
```

The response contains `{ conversation, messages: [userMessage, assistantMessage], mode: "demo" }`. Each message has `id`, `conversationId`, `requestId`, `role`, `content`, `sequence`, and `createdAt`. List responses use `{ data, pagination: { page, pageSize, totalItems, totalPages } }`.

Retrying the same request ID and content returns the already-saved turn. Reusing an ID for different content returns 409. A stale conversation version also returns 409; reload before trying a new turn. A database row lock serializes commits, and both messages are saved in one transaction. Orchestration runs before the transaction so a future hosted inference call does not hold a database lock. Concurrent retries can still execute inference more than once before commit; database idempotency prevents duplicate saved turns, not duplicate provider charges.

Titles are trimmed and limited to 120 characters; user messages to 4,000 characters. Clients cannot supply an assistant/system role, timestamps, or message IDs. Invalid UUIDs, unknown fields, and invalid pagination return 400. Missing conversations return 404. Errors use `{ error: { code, message, details? } }`; internal exceptions do not expose database details or stack traces.

## Orchestrator boundary

`src/orchestrator/orchestrator.service.ts` calls `AGENT_BASE_URL/v1/executions` with
`question`, the backend conversation UUID as `conversation_id`, and the complete ordered
PostgreSQL `history` of user/assistant messages preceding that question. Set `AGENT_BASE_URL`
(default `http://127.0.0.1:8000`), optional matching `AGENT_API_KEY`, and
`AGENT_REQUEST_TIMEOUT_MS` (default 130000). Qwen credentials belong only in
`../agent-orchestrator/.env`. Start retrieval and the agent before querying; see the root README.
The Docker API profile uses `host.docker.internal:8000` by default.

Replies preserve the agent's answer text and citations. Agent outages, busy responses, and
invalid responses fail the turn rather than returning canned content. The backend reports
`mode: agent`; demo inference on the agent service is explicitly labelled in the answer.
Reasoning state lives in the agent, while PostgreSQL supplies authoritative chat context on every
request. The agent sends that transcript to Qwen as user/assistant messages. History exceeding
200 messages or a 64-KiB request is rejected explicitly instead of silently truncated. Attachments are
stored for download, not automatically analysed or indexed. Deleting a UI chat does not remove
its agent checkpoint. A browser abort stops waiting but does not undo agent execution.
Concurrent retries can still execute inference more than once before a deduplicated commit.

## Frontend connection

The frontend uses `http://127.0.0.1:3001/api` by default. Override `NEXT_PUBLIC_API_URL` in the frontend environment when needed, then restart/rebuild Next.js. `FRONTEND_ORIGINS` in this backend is an exact comma-separated origin allowlist.

Conversation history is now server-backed. Existing browser-only demo history is not automatically uploaded or deleted. The UI paginates conversation history and can load earlier messages.

## Configuration and local scope

See `.env.example`. To use another PostgreSQL server, set `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME`; set `DB_SSL=true` for TLS with certificate verification. Keep real credentials out of version control. `synchronize` is disabled; change the schema through reviewed migrations. `npm run db:revert` rolls back the latest migration and can remove chat tables/data.

This is a local, shared, single-workspace starter. Authentication, per-user ownership, PII redaction, and production access control are not implemented. Add these before making the API publicly accessible. CORS is not authentication. Demo replies do not execute consequential actions.

There is no model-response cache yet; saved conversation history and request deduplication are database features.

## Browser rate limiting

Every conversation request, including uploads and downloads, requires `X-Client-ID` containing a UUID v4. The frontend generates it once and saves it as `operations_copilot_client_id` in localStorage. Reloads and tabs on the same origin reuse it. Missing or malformed IDs return 400. UUIDs are normalized to lowercase. Health checks, CORS preflight, and Swagger documentation do not consume quota or require the header.

Defaults are centralized in [`src/config/rate-limit.config.ts`](src/config/rate-limit.config.ts). Override them in `.env`; invalid configuration prevents startup:

| Setting | Default | Meaning |
| --- | --- | --- |
| `RATE_LIMIT_API_COUNT` | `120` | Attempts across all controller API routes per browser per window |
| `RATE_LIMIT_API_WINDOW_SECONDS` | `60` | General API window |
| `RATE_LIMIT_CHAT_COUNT` | `20` | Message submissions across all conversations per browser per window |
| `RATE_LIMIT_CHAT_WINDOW_SECONDS` | `60` | Message-generation window |
| `RATE_LIMIT_BLOCK_SECONDS` | `60` | Cooldown starting when a request exceeds a quota |
| `REDIS_URL` | `redis://127.0.0.1:6380` | Host-development Redis; Compose overrides to `redis://redis:6379` |
| `RATE_LIMIT_KEY_PREFIX` | `operations-copilot:rate-limit:v1` | Shared namespace for API replicas |

Counts and durations must be positive integers; durations are capped at 24 hours. Restart a host API after editing `.env`. For Docker, rerun `docker compose --profile app up -d --build --wait`. All replicas must use the same Redis, prefix, and policy settings. Changing `REDIS_PORT` also requires updating the host-development `REDIS_URL`.

The guard checks Redis before controller execution, attachment processing, database operations, or agent calls. Each policy uses a first-request fixed window and a Redis hash keyed as `<prefix>:<browser-uuid>:api` or `<prefix>:<browser-uuid>:chat`, holding `count`, `resetAt`, and `blockedUntil`. A Lua script uses Redis time to read, increment, block, and set expiry atomically. Concurrent API replicas share quotas. PostgreSQL is not queried for quota tracking.

Exactly the configured number of attempts is allowed; the next attempt returns 429. Requests that later fail validation still count. Chat attempts also consume general API quota, including attempts while the chat policy is blocked. A chat-only block leaves other routes available unless the general quota is exhausted. Blocked requests never extend that policy's cooldown. A new window starts after cooldown expiry; ordinary expired windows also reset automatically. Unused entries expire in Redis.

Blocked responses include a standard `Retry-After` header and:

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests. Please try again in 60 seconds.",
    "retryAfterSeconds": 60
  }
}
```

The actual retry value reflects the remaining cooldown. Allowed responses include `X-RateLimit-Limit-api`, `X-RateLimit-Remaining-api`, and `X-RateLimit-Reset-api` (seconds until reset), plus corresponding `-chat` headers on message submissions. CORS exposes these headers and `Retry-After`. Redis outages reject protected requests with 503, without an in-memory fallback. Readiness also checks Redis.

For curl or Postman, generate a UUID once and reuse it:

```bash
CLIENT_ID="$(uuidgen)"
curl -H "X-Client-ID: $CLIENT_ID" http://127.0.0.1:3001/api/conversations
```

This controls browser usage; it does not authenticate users. Clearing localStorage, using another browser/origin, or supplying a new UUID creates a new quota. Use verified authenticated user IDs for enforceable account quotas. Redis binds to loopback for local development. For remote Redis, supply credentials and `rediss://` TLS in `REDIS_URL`; keep credentials out of logs and version control.

## Checks

```bash
npm run lint
npm run typecheck
npm test
npm run test:e2e
npm run build
```

Integration tests use a distinct `TEST_DB_NAME` (default `operations_copilot_test`) on the configured PostgreSQL server. The preparation script creates it if missing and refuses to use the main database. Tests run migrations and remove only their own test conversations. They verify CRUD/search/pagination, retries, stale versions, concurrent writes, restart persistence, cascading deletion, validation, request-size limits, and throttling. Use a local database role allowed to create this test database.

## Layout

- `src/conversations/`: HTTP contract and conversation persistence.
- `src/orchestrator/`: Server-side model/agent integration boundary.
- `src/database/`: Entities, data source, and migrations.
- `src/config/`: Startup configuration validation.
- `src/common/`: Consistent API error handling.
- `compose.yaml`: Persistent PostgreSQL and Redis plus optional API container.

## References

- [NestJS database integrations](https://docs.nestjs.com/techniques/database)
- [NestJS validation](https://docs.nestjs.com/application/validation)
- [TypeORM migrations](https://typeorm.io/docs/migrations/setup/)
- [NestJS rate limiting](https://docs.nestjs.com/security/rate-limiting)

## Chat attachments

`POST /api/conversations/:id/messages` also accepts `multipart/form-data` with the usual `content`, `requestId`, and `expectedVersion` fields plus up to five repeated `files` parts. The content field may be an empty string when files are attached; the stored user message then reads `Attached files`. Text-only JSON requests keep their existing behaviour.

Allowed extensions: PDF, DOCX, XLSX, TXT, MD, CSV, JSON, PNG, JPG/JPEG, WEBP, and GIF. Each file must be nonempty and at most 5 MiB (5,242,880 bytes). Names are limited to 180 characters without path separators or control characters. Binary formats are checked by detected file signature; text must be UTF-8, and JSON must parse successfully. Browser MIME headers are not trusted as proof of file type. This validation is not malware scanning.

Attachment bytes are stored in PostgreSQL `bytea` columns, inside the same transaction as the turn. The existing database volume therefore also persists attachments; no extra upload directory or file-storage volume is required. Deleting the conversation cascades through messages to its attachment rows. Metadata includes ID, name, MIME type, byte size, and position. Binary data and internal retry hashes are excluded from message JSON.

`GET /api/conversations/:id/attachments/:attachmentId` downloads a file only when it belongs to that conversation. It sends the original bytes as `application/octet-stream` with attachment disposition, an encoded filename, and a private/no-store cache policy. Files are downloaded rather than rendered inline as HTML. This is still the existing local shared workspace, without user authentication or ownership isolation.

Retries compare both message text and the names/content hashes of the files in selection order. Reusing a request ID with changed attachments returns 409; retries do not create additional stored files. Type/size failures return 400, 413, or 415 without committing a partial message.

The attachment migration is applied automatically when the API container starts. After updating the code, rebuild the API image with `docker compose --profile app up -d --build --wait`. For host development, run `npm run db:migrate` before starting the API. The demo orchestrator acknowledges uploads but does not extract or analyse document/image content.
