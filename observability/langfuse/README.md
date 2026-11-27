# Langfuse trace dashboard

Langfuse records every question the agent answers as a **trace**: the planner's
decision, each retrieval call, every Qwen call with its input, output, tokens and
latency, the critic's verdict with the exact issues it found, and why the agent
stopped if it abstained. It runs locally in Docker.

Tracing is optional. The agent sends traces only when `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` are set in `agent-orchestrator/.env`; without them it behaves
exactly as before.

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | The official Langfuse self-hosting file, unchanged |
| `docker-compose.override.yml` | Moves the web UI to port 3100 (3000 is the RegAssist frontend) and keeps all ports local |
| `make_env.py` | Writes `.env` with random passwords, a ready-made RegAssist project, a login and API keys |

Langfuse runs seven containers: web, worker, AI gateway, PostgreSQL, ClickHouse, Redis
and MinIO. Give Docker Desktop at least 4 GB of memory (**Settings → Resources**).
Its ports (3100, 3030, 3031, 5432, 6379, 8123, 9000, 9090, 9091) don't clash with
RegAssist's (3000, 3001, 5433, 6380, 8000, 8001).

## First-time setup

From this folder (Windows paths shown):

```powershell
cd observability\langfuse
python make_env.py
```

It prints three lines for the agent and your Langfuse login. Copy them somewhere safe;
they are also in `observability/langfuse/.env`, which Git ignores.

Add the three lines to `agent-orchestrator/.env`:

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=http://127.0.0.1:3100
```

Then install the tracing package into the agent's environment once (it is listed in
`pyproject.toml`, so a fresh install includes it):

```powershell
cd ..\..\agent-orchestrator
.venv\Scripts\pip install -e ".[test]"
```

## Start Langfuse

With Docker Desktop running:

```powershell
cd observability\langfuse
docker compose up -d
```

The first start downloads the images (a few GB) and takes several minutes. Langfuse
is ready when `docker compose ps` shows `langfuse-web` as running and
http://localhost:3100 shows the login page. Sign in with the email and password that
`make_env.py` printed; the RegAssist project already exists.

Restart the agent (Terminal 2) so it reads the new `.env` lines. Ask a question in
the chat window, and within a few seconds a trace appears under **Tracing** in Langfuse.

If Langfuse isn't running while the agent has the keys set, answers still work normally;
the agent's terminal just shows "Failed to export spans" warnings. Start Langfuse, or
empty the two `LANGFUSE_` keys and restart the agent, to silence them.

To stop Langfuse: `docker compose stop` (keeps your traces). `docker compose down -v`
deletes everything, including all traces; after that, delete `.env` and run
`make_env.py` again.

## Reading a trace

Each question is one trace named `regassist-query`, grouped by conversation under
**Sessions**:

```text
regassist-query (agent)          question in, answer out; level WARNING if the agent abstained
├─ resolve_context (chain)
├─ planner (chain)
│  └─ qwen.planner (generation)  what the planner was given, its JSON plan, tokens, latency
├─ executor (chain)
│  ├─ tool.search_documents (retriever)       query in, ranked document IDs out
│  ├─ tool.fetch_passages (retriever)         citation IDs, sections, pages, text preview
│  ├─ tool.lookup_document_status (tool)      applicable / not applicable, with the reason
│  └─ qwen.executor (generation)              the draft answer
├─ critic (evaluator)
│  ├─ qwen.critic (generation)
│  └─ critic.verdict (event)     decision, the five checks, and the exact issues
├─ (executor and critic again if a revision was needed)
├─ control.stop (event)          only when the agent stops early: the stop code and reason
└─ finish (chain)
   └─ qwen.state_summary (generation)
```

Useful views for the evaluation report and the demo:

- **A trace's timeline** shows where the time goes; Qwen calls usually dominate.
- **`critic.verdict`** shows why a draft was revised or rejected, for example
  "Claim 1 contains a number absent from its cited passages."
- **Filter by level = WARNING** to list every question the agent abstained on.
- **Dashboards** chart latency, token usage and trace counts over time.

Traces contain the questions, retrieved passages and model outputs. Keep to synthetic
or public material (RBI directions and tax documents are public).

## Sharing traces for the submission

A local Langfuse at `localhost:3100` can't be opened by anyone else, and the report needs
links graders can open. For the submitted traces, use **Langfuse Cloud** instead (the free
Hobby plan is enough; it keeps traces viewable for 30 days, so generate them shortly before
submitting):

1. Sign up at https://cloud.langfuse.com (EU) or https://us.cloud.langfuse.com (US), create
   a project, and create an API key pair under **Settings → API Keys**.
2. In `agent-orchestrator/.env`, use the cloud keys and make traces public:

   ```dotenv
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_BASE_URL=https://cloud.langfuse.com
   LANGFUSE_PUBLIC_TRACES=1
   ```

   With `LANGFUSE_PUBLIC_TRACES=1`, anyone with a trace's link can view it without logging
   in, and the agent prints each trace's link in its terminal. Questions and answers in
   those traces become public, so use this only for the showcase run.
3. Restart the agent, then run the showcase (retrieval service and agent running):

   ```powershell
   cd agent-orchestrator
   .venv\Scripts\python scripts\trace_showcase.py
   ```

   It asks ten questions in one conversation, covering cited RBI and tax answers, a
   follow-up, version awareness, two refusals, prompt injection, clarification and
   abstention. It writes `showcase/<session>.md` with every trace link and the session
   link, and marks any question that didn't behave as intended. `--dry-run` lists the
   questions; `--questions my.json` uses your own.
4. In Langfuse, open **Sessions**, open the showcase session and publish it: that one link
   opens all ten traces. Then set `LANGFUSE_PUBLIC_TRACES=0` again and restart the agent.
5. In the report, include the session link, two or three individual trace links, and
   screenshots of an expanded trace and of a dashboard (dashboards can't be shared by
   link, and screenshots survive after the 30 days).

Every response from the agent also carries its trace ID in the `X-Trace-Id` header.
