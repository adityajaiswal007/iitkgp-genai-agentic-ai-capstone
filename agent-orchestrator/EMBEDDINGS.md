# Hosted query embeddings in the agent orchestrator

The orchestrator now has a reusable `EmbeddingClient` and `POST /v1/query-embeddings`.
They call the supplied English embedding endpoint. No files in `document-retrieval` are changed.
No embedding call is inserted into the existing planner/executor/critic loop or its retrieval
requests: the current retrieval API accepts text queries, not a precomputed query vector.

## Configure your server

Merge `.env.embeddings.example` into your existing `.env`, preserving your Qwen and RAG settings:

```dotenv
EMBEDDING_BASE_URL=https://103.42.50.41:1035/v1
EMBEDDING_MODEL=bge-small-en-v1.5
EMBEDDING_CA_CERT=/certificate/embedding.crt
EMBEDDING_API_KEY=
EMBEDDING_TIMEOUT_SECONDS=15
EMBEDDING_DIMENSIONS=
```

Use a base URL ending in `/v1`; the client appends `/embeddings`. The model value is sent exactly
as configured. `EMBEDDING_API_KEY` is optional because the supplied example did not specify
authentication. Set it if this server requires a bearer token. It is independent of `QWEN_API_KEY`
and the application's own `AGENT_API_KEY`.

`EMBEDDING_CA_CERT` must point to the actual trusted PEM CA/bundle, not a server private key.
The given `/certificate/embedding.crt` works if that file exists on the host. If it lives elsewhere,
set its actual absolute host path. Local Python also expands `~` and environment variables.
When configured, a missing or invalid CA fails at startup. TLS and hostname verification stay enabled.
Leave `EMBEDDING_BASE_URL` blank to keep embedding access disabled; existing execution still works.

Local Python:

```sh
python -m uvicorn regulatory_agent.api:app --env-file .env --host 127.0.0.1 --port 8000
```

Docker, with the embedding certificate mounted read-only:

```sh
docker compose -f compose.yaml -f compose.embeddings.yaml up -d --build
```

If you also use the Qwen certificate override, include both:

```sh
docker compose -f compose.yaml -f compose.qwen.yaml -f compose.embeddings.yaml up -d --build
```

The embedding override changes only the `agent` service. It maps your host certificate to
`/certificate/embedding.crt` inside the container, retains the SQLite volume, and never bakes
certificates or credentials into the image. Use your existing retrieval-service setup separately;
this change does not configure its encoder, rebuild its index, or alter its API contract.

## Request vectors through the orchestrator

```sh
curl -sS http://localhost:8000/v1/query-embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":["How do I raise a bank complaint?","Which documents support this tax deduction?"]}'
```

When `AGENT_API_KEY` is set, also send `X-API-Key` as with `/v1/executions`. The upstream embedding
credential is never supplied by API callers. The response follows the provider's list shape and
adds `dimensions`:

```text
object: "list"
model: "bge-small-en-v1.5"
dimensions: actual validated dimension
data:
  - object: "embedding"
    index: 0
    embedding: [the full vector for the first input]
  - object: "embedding"
    index: 1
    embedding: [the full vector for the second input]
usage:
  prompt_tokens: provider-reported count
  total_tokens: provider-reported count
```

Vectors are returned in input order even if the server returns entries out of order. The route
returns full vectors; it does not echo query text or store queries/vectors in conversation state.
Missing provider usage is returned as `null`, not a fabricated token count.

For a Python consumer:

```python
from regulatory_agent.embeddings import EmbeddingClient

client = EmbeddingClient(
    base_url="https://103.42.50.41:1035/v1",
    model="bge-small-en-v1.5",
    ca_cert="/certificate/embedding.crt",
    api_key="",  # Set if this server requires a bearer token.
)
try:
    batch = client.embed_queries(["How do I raise a bank complaint?"])
    query_vector = batch.data[0].embedding
    print(batch.model, batch.dimensions)  # Avoid logging actual customer text/vectors.
finally:
    client.close()
```

## Where to use the vectors

| Use | Value | Current boundary |
| --- | --- | --- |
| Dense query retrieval | Match an English question to relevant indexed passages by meaning | Primary intended integration; the retrieval team owns the index and search contract |
| Hybrid retrieval | Combine dense candidates with BM25 before the existing reranker | Retrieval-team work; no ranking or candidate-fusion changes made here |
| Attachment passage selection | Rank passages within the user's uploaded files semantically | Possible future orchestrator enhancement; current attachment selection is preserved |
| Conversation memory selection | Select relevant earlier turns within an authorized conversation | Possible future enhancement; no vector memory store or cross-conversation lookup added |
| Query similarity/evaluation | Obtain vectors for a downstream evaluation or analysis client | Available now through the batch endpoint/Python client; scoring belongs to its consumer |

A retrieval handoff should agree on **model/version, output dimension, query prefix, normalization,
similarity metric, corpus snapshot and authority/date/year filters**. Dimension equality alone is
not sufficient: vectors from different embedding models are not interchangeable. Existing document
vectors must use a compatible model and preprocessing before these query vectors can search them.
The client neither adds query instructions nor normalizes vectors, so the provider request matches
the supplied payload. Coordinate any BGE query-prefix policy with the index owner.

Do not add a `query_embedding` field to current search requests without a coordinated contract
change: the current service validates its schema strictly. The retrieval team can choose to
call the provider itself or consume this endpoint when it supports externally supplied vectors.
No document chunks are automatically sent to the hosted embedding server by this integration.
Vectors are numeric search features; passing them directly to Qwen's text prompt does not provide
document context. The LLM still needs retrieved text passages and citations.

Embeddings do not validate regulation applicability, prove claim grounding, replace the critic,
or make near-duplicate financial/legal questions safe to answer from a cached response. Any later
semantic cache must account for date/year, authority, entity details and corpus/version changes.

## Bounds, failures, and accounting

- One provider request per batch, with no automatic retries or fallback to a different model.
- At most 8 nonblank queries, 2,000 characters each, and 16 KiB combined UTF-8 text.
- A 1 MiB response cap, native HTTP timeouts, and an elapsed-time check while streaming the response.
- Response indices/count must match the request. Vectors must be finite, nonzero, and have the same
  length. A truncated example with different vector lengths is rejected rather than silently padded.
- Optional `EMBEDDING_DIMENSIONS` enforces the dimension agreed with the retrieval team. Otherwise
  the actual consistent batch dimension is returned; no dimension is inferred from an abbreviated sample.
- The route shares the service's execution-capacity lock: concurrent execution/embedding requests
  receive `SERVICE_BUSY` (409). It uses the existing optional service-key protection.
- Unconfigured provider: 503 `EMBEDDING_NOT_CONFIGURED`; invalid caller input: 422; timeout: 504;
  provider connection/rate-limit/server failure: 503; malformed provider output or rejected provider
  request: 502. Provider response bodies and internal details are suppressed.
- Embedding usage is returned separately from agent execution metrics. Input bounds are not an
  aggregate team quota. Enforce shared quotas at the provider or via a durable application ledger.
  If embedding calls are later added inside the agent loop, include them in its step/token/deadline
  accounting rather than hiding an extra paid model call inside a tool.

## Verification

Run `python -m pytest -q` from the orchestrator environment. The embedding tests cover payloads,
optional bearer auth, batch ordering, malformed JSON/vectors, inconsistent dimensions, timeouts,
response-size bounds, API authorization, capacity guards, and actual local HTTPS certificate checks.
They make no inference calls to the supplied external server. Live verification requires access
to that server and its actual certificate, plus a key if required.
