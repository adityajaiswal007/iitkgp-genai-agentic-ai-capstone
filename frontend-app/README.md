# Operations Copilot Frontend

The frontend provides a ChatGPT-style interface for the Logistics Operations RAG Assistant described in the [project overview](../README.md). Users can manage conversations, send messages, attach documents, and read assistant replies in a responsive workspace.

## Directory status

This directory currently contains documentation only. The application source, dependency files, tests, and Docker configuration have not yet been added to this repository folder.

The details below describe the existing reference frontend and the setup to use after importing its source. The run commands require the corresponding `package.json`, lockfile, `.nvmrc`, Dockerfile, and Compose configuration to be present. Backend services must also be available.

## Application features

| Feature | Behaviour |
| --- | --- |
| Chat workspace | Responsive layout with desktop navigation and a mobile sidebar. |
| Conversations | Create, reopen, search, paginate, and delete conversations. |
| Message composer | Enter sends a message; Shift+Enter inserts a new line. |
| Typing effect | Shows a typing indicator for at least one second, then reveals new replies in small chunks with a cursor. |
| Stop response | Stops the visual reveal; reopening the conversation displays the full saved reply. |
| Copy response | Copies completed assistant text to the clipboard. |
| Attachments | Select files using the paperclip button or drag them onto the composer. Remove queued files before sending. |
| Saved files | Download attachments from messages after reopening or reloading a conversation. |
| Error recovery | Displays connection and validation errors, and retains drafts and selected files when a request fails. |

Saved conversation history opens immediately without replaying the typing effect. The typing effect is frontend playback of a complete API response; it is not server-side token streaming.

### Attachment support

- Up to **five files per message**.
- Maximum **5 MiB per file** (5,242,880 bytes).
- Supported extensions: `.pdf`, `.docx`, `.xlsx`, `.txt`, `.md`, `.csv`, `.json`, `.png`, `.jpg`, `.jpeg`, `.webp`, and `.gif`.
- Files can be sent with or without accompanying text.
- The browser checks file selection limits; the backend also validates size, names, and file content.
- File bytes are stored with the message in PostgreSQL through the backend API. Deleting a conversation also deletes its attachments.

Uploading a file does not automatically add it to the RAG knowledge base. Document and image analysis are not connected in the reference implementation.

## Technology stack

| Technology | Purpose |
| --- | --- |
| Next.js 16 with App Router | Application framework and production server. |
| React 19 | Interactive components and client state. |
| TypeScript | Shared types and compile-time checks. |
| Tailwind CSS 4 and application CSS | Styling and responsive layouts. |
| Lucide React | Interface icons. |
| Playwright | Browser tests for chat, typing effects, and attachments. |
| Docker and Docker Compose | Container builds and local stack startup. |

## Architecture

```text
Browser / Next.js frontend
          |
          | HTTP API: JSON messages and multipart file uploads
          v
NestJS backend / orchestrator
          |
          v
PostgreSQL: conversations, messages, and attachment bytes
```

The frontend communicates with the NestJS API; it does not connect directly to PostgreSQL. The backend owns persistence, upload validation, request limits, and future hosted model integration.

## Local development

After the application source has been added:

1. Install Node.js 24 and npm. The reference application includes a `.nvmrc` file for nvm users.
2. Start the NestJS backend and its PostgreSQL database.
3. From `frontend-app`, run:

   ```bash
   nvm use
   npm ci
   npm run dev
   ```

Open [http://localhost:3000](http://localhost:3000). If nvm is not installed, use Node.js 24 directly and run the npm commands.

### API configuration

The default browser-facing API URL is:

```dotenv
NEXT_PUBLIC_API_URL=http://127.0.0.1:3001/api
```

To override it, copy `.env.example` to `.env.local`, update the URL, and restart the development server. For Docker or production builds, set this variable when building the image: Next.js embeds public environment variables in the frontend bundle.

This URL must be reachable from the user's browser. The backend's CORS allowlist must include the frontend origin. Hosted inference credentials belong in the backend environment; never put secrets in `NEXT_PUBLIC_*` variables.

## Run with Docker

These instructions apply after the Docker files and backend source have been imported. The reference Compose configuration expects this layout:

```text
iitkgp-genai-agentic-ai-capstone/
├── frontend-app/
│   ├── Dockerfile
│   ├── compose.yaml
│   └── ...application source
└── backend/
    ├── Dockerfile
    ├── compose.yaml
    ├── .env
    └── ...backend source
```

The frontend Compose file includes `../backend/compose.yaml` and reads `../backend/.env`. Update its include paths if the backend is placed elsewhere. Configure the backend environment before starting the stack.

With Docker Desktop running, execute this single command from `frontend-app`:

```bash
docker compose --profile app up -d --build --wait
```

This builds and starts the frontend, NestJS backend, and PostgreSQL. The backend applies database migrations before startup, and the frontend waits for the API health check.

| Service | Local address |
| --- | --- |
| Frontend | [http://localhost:3000](http://localhost:3000) |
| API documentation | [http://localhost:3001/api/docs](http://localhost:3001/api/docs) |
| API health check | [http://localhost:3001/api/health](http://localhost:3001/api/health) |
| PostgreSQL | `127.0.0.1:5433` |

Useful commands from the same folder:

```bash
# Check service status
docker compose --profile app ps

# Follow frontend logs
docker compose --profile app logs -f frontend

# Stop services while preserving database data
docker compose --profile app stop
```

The reference configuration uses a persistent PostgreSQL volume for both chat history and attachments. Its Dockerfile uses Next.js standalone output and runs the frontend as an unprivileged user.

## Available npm commands

These scripts are provided by the reference application's `package.json`:

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the development server. |
| `npm run build` | Create a production build. |
| `npm start` | Run the production server after building. |
| `npm run lint` | Run ESLint. |
| `npm run typecheck` | Run TypeScript checks. |
| `npm test` | Run Playwright browser tests. |

Before the first browser test run, install the test browser:

```bash
npx playwright install chromium
```

Chat and attachment integration tests require a running backend and database. Typing-effect tests use controlled API responses and can run independently:

```bash
npm test -- tests/reply-playback.spec.ts
```

## Expected application structure

```text
frontend-app/
├── src/
│   ├── app/                     # Routes, layout, metadata, and global styles
│   ├── components/chat/         # Workspace, sidebar, composer, and messages
│   │   ├── use-conversations.ts # API-backed conversation state
│   │   └── use-reply-playback.ts # Typing indicator and gradual reply display
│   └── lib/
│       ├── chat.ts              # Conversation, message, and attachment types
│       ├── chat-client.ts       # API client and response validation
│       └── attachments.ts       # File selection limits and size formatting
├── tests/                      # Browser tests
├── .env.example
├── .nvmrc
├── Dockerfile
├── compose.yaml
└── package.json
```

## Current limitations

The reference frontend uses explicitly labelled demo replies from the backend. Hosted inference, document retrieval, source citations, and agent execution are not yet connected. Authentication, user-specific workspaces, and PII redaction are also pending; the current application is intended for a local, shared workspace using sample content.
