# Worker web interface

The Next.js interface is the primary experience for maintenance workers. It
uses operational language and keeps framework, model and API details away from
the normal question-and-manual workflow.

## Information architecture

The interface has three focused areas:

- **Assistant** is the default workspace. Workers can ask a free-text question,
  optionally restrict it by manual, brand, machine, site/area and document type,
  use common-question starters and inspect the exact evidence behind an answer.
  The vertical navigation lists every saved conversation below Assistant, newest
  first in a scrollable region. Workers can reopen, continue or delete a thread.
- **Manuals** provides drag-and-drop or file-picker upload, current and retained
  revision views, replacement, re-indexing, archiving and confirmed permanent
  deletion. It accepts digital or scanned PDFs, PNG/JPEG document images, text
  and Markdown. Upload and revision forms use searchable create-or-select
  controls. Workers can reuse saved values, type a new value and press Enter,
  or attach several brands, machines, sites and document types to one manual.
- **Settings** manages the API keys required by fixed external services, uses a
  compact operational status table for service readiness, and shows
  non-sensitive request, embedding-cache, SQLite and developer information.

The navigation becomes a drawer on narrow screens. Tables and answer sources
collapse progressively so important actions remain usable on a workshop tablet
or phone.

## Answer presentation

The UI does not turn the API response into an opaque chat bubble. A grounded
answer is presented as a task-focused result with:

- visible inline markers such as `[S1]`;
- the number of available citations;
- expandable source cards;
- the source manual, page or section and similarity score;
- the exact evidence excerpt; and
- a reminder to confirm critical work against approved site procedures.

Each assistant response has thumbs-up and thumbs-down controls. Selecting the
active rating again clears it; selecting the other control replaces it. Ratings
are stored against the assistant message in SQLite, not in browser storage.

When the providers are not configured, the question action is disabled and the
readiness state directs users to Settings. API failures use the safe message
returned by FastAPI rather than exposing stack traces or internal details.

## Server-side API boundary

Browser requests use `/api/backend/...`. A Next.js App Router route handler
forwards those requests to `AMA_API_BASE_URL`, which defaults to
`http://127.0.0.1:8000` during local development and is `http://api:8000` in
Docker Compose.

This boundary avoids browser CORS configuration and prevents internal service
addresses or credentials from becoming frontend configuration. It is a thin
transport layer: FastAPI remains responsible for validation, ingestion,
retrieval, answer generation and stable error codes.

Conversation history uses the same proxy. The browser holds only the currently
displayed transcript; durable messages live in the local SQLite data volume
rather than `localStorage`.

## Local development

Start the API in one terminal:

```bash
ama-api
```

Start the interface in another:

```bash
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. Set `AMA_API_BASE_URL` before `npm run dev` only
when the API uses a different local address.

## Current boundary

The interface is local and unauthenticated. It does not yet provide user roles
or streaming answers. Settings accepts supported API keys, but does not expose
provider or model selection. Complete secrets are never returned to the browser;
saved values are shown only as masks and edits use an empty password field.
