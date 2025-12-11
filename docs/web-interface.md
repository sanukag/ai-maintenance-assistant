# Worker web interface

The Next.js interface is the primary experience for maintenance workers. It
uses operational language and keeps framework, model and API details away from
the normal question-and-manual workflow.

## Information architecture

The interface has three focused areas:

- **Assistant** is the default workspace. Workers can ask a free-text question,
  optionally restrict it to one manual, use common-question starters and inspect
  the exact evidence behind an answer.
- **Manuals** provides drag-and-drop or file-picker upload, current and retained
  revision views, replacement, re-indexing, archiving and confirmed permanent
  deletion. It accepts digital or scanned PDFs, PNG/JPEG document images, text
  and Markdown.
- **Settings** shows service readiness, active provider models, local-data and
  privacy boundaries, local OCR availability, API documentation and developer
  runtime information. It also reports whether image and diagram understanding
  is active and which model provides it.

The navigation becomes a drawer on narrow screens. Tables and answer sources
collapse progressively so important actions remain usable on a workshop tablet
or phone.

## Answer presentation

The UI does not turn the API response into an opaque chat bubble. A grounded
answer is presented as a task-focused result with:

- visible inline markers such as `[S1]`;
- a `Sources verified` state;
- expandable source cards;
- the source manual, page or section and similarity score;
- the exact evidence excerpt; and
- a reminder to confirm critical work against approved site procedures.

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

The interface is local and unauthenticated. It does not yet provide user roles,
saved question history, feedback capture or streaming answers.
Settings deliberately reports configuration but does not edit provider values
or accept API keys in the browser.
