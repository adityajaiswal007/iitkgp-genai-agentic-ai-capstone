# Operations Copilot frontend

A ChatGPT-style workspace built with Next.js 16, React 19, TypeScript, Tailwind CSS 4, and Lucide icons. Includes responsive navigation, searchable server-backed chat history, new/delete actions, sample replies, stop/copy controls, and keyboard composition.

## Run locally

Start the PostgreSQL database and NestJS backend in `../backend` first; see that project's README. Then use Node.js 24 (specified in `.nvmrc`):

```bash
nvm install
nvm use
npm ci
npm run dev
```

Open http://localhost:3000. Enter sends a message; Shift+Enter adds a new line.

The default API URL is `http://127.0.0.1:3001/api`. To change it, copy `.env.example` to `.env.local`, set `NEXT_PUBLIC_API_URL`, then restart/rebuild Next.js. This URL is public browser configuration, not a place for provider keys.

## Current scope

Conversations and messages are persisted in PostgreSQL through the NestJS API. The frontend no longer writes chat history to localStorage. Previous browser-only demo conversations are not automatically uploaded or removed. The sidebar searches the server and paginates history; longer conversations can load earlier messages.

Assistant replies are predefined backend samples, explicitly labelled. No hosted model, retrieval service, or multi-agent workflow is connected. This is a shared local workspace without authentication or PII redaction; use sample content. The backend supplies request limits and validation. Saved history is not a model-response cache.

New replies show a typing indicator for at least one second from submission, then reveal two words every 45 milliseconds with a typing cursor. This is frontend playback of a complete API response. Saved history opens immediately without replaying. Stop freezes the visible text; reopening the conversation shows the full saved reply.

The UI stops waiting when you cancel. A request already accepted by the server may still be saved; reopen the conversation to refresh it. Failed message requests retain the draft, and retries reuse a request ID to prevent duplicate stored turns.

## Backend integration

`src/lib/chat-client.ts` implements the NestJS HTTP contract, response validation, and AbortSignal support. `src/components/chat/use-conversations.ts` handles server state and UI transitions. Extend the shared types when adding streamed responses, source citations, and agent status events. Remove demo labels only when real inference is connected.

Hosted inference keys stay in the backend environment. Never put secrets in `NEXT_PUBLIC_*` variables.

## Checks

Keep the backend and PostgreSQL running while testing:

```bash
npm run lint
npm run typecheck
npx playwright install chromium
npm test
npm run build
npm start
```

Alternatively, point `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` at an installed compatible Chrome executable. Tests reuse/start Next.js on port 3000, exercise the real backend, and delete only the conversations they create. They require the backend URL configured for the frontend. CI should run `npx playwright install --with-deps chromium` first.

Typing-effect tests can run with controlled API responses while the backend is stopped: `npm test -- tests/reply-playback.spec.ts`. These cover the one-second delay, incremental text, stopping, navigation, deletion, errors, and saved-history playback.

## Docker

With Docker Desktop running, start the frontend, NestJS API, and PostgreSQL together from this folder:

```bash
docker compose --profile app up -d --build --wait
```

Open http://localhost:3000. The API documentation is available at http://localhost:3001/api/docs. The command builds both application images, starts PostgreSQL, runs database migrations, and waits for health checks. The frontend starts after the API is healthy.

`compose.yaml` includes `../backend/compose.yaml` and reads the existing `../backend/.env`. It uses the same `operations-copilot` project name and PostgreSQL volume as backend-only startup, preserving existing chats and attachments. Keep the frontend and backend folders beside each other. Docker Compose 2.20.3 or newer is required for includes.

Inspect the stack with `docker compose --profile app ps`. Stop all three services with `docker compose --profile app stop`; the database volume is preserved. To view frontend logs, run `docker compose --profile app logs -f frontend`.

The frontend defaults to the browser-accessible API at `http://127.0.0.1:3001/api`. For another API URL, run `NEXT_PUBLIC_API_URL=https://your-api.example/api docker compose --profile app up -d --build --wait`. Next.js embeds this public URL at build time. The image uses standalone output and runs as the unprivileged `node` user. All published ports bind to loopback for local use.

## References

- [Next.js installation](https://nextjs.org/docs/app/getting-started/installation)
- [Next.js standalone deployment](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)

## Chat attachments

Use the paperclip button or drop files onto the composer. Up to five files can be selected per message, with a maximum of 5 MB per file. Supported extensions: PDF, DOCX, XLSX, TXT, MD, CSV, JSON, PNG, JPG/JPEG, WEBP, and GIF. Remove a selected file with its X button before sending. A file can also be sent without accompanying text.

Selected files are sent as multipart form data and stored with the user message in PostgreSQL. Saved messages show download links after a reload. Downloading returns the original bytes. Failed uploads retain the selected files and draft so the user can retry. The existing typing effect applies to replies after upload as well.

File analysis is not connected. The demo reply explicitly says the files were saved and have not been read or analysed. Uploading an attachment does not automatically add it to a RAG corpus.
