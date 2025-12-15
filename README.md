# AI Maintenance Assistant

[![CI](https://github.com/sanukag/ai-maintenance-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/sanukag/ai-maintenance-assistant/actions/workflows/ci.yml)

A local-first assistant for turning maintenance documents into useful,
traceable knowledge.

The application includes a worker-facing Next.js interface for asking grounded
questions, managing approved manual revisions and checking system readiness
without developer tools.

It also includes a guided fault-investigation workspace. The assistant asks
focused questions, retains observations and measurements, ranks competing
hypotheses, answers follow-ups and proposes only evidence-backed checks within
an explicit worker-selected safety boundary.

## Development setup

The project requires Python 3.12 or later. Non-container OCR also requires the
local Tesseract executable and English language data.

On macOS, install it with `brew install tesseract`. On Debian or Ubuntu, use
`sudo apt-get install tesseract-ocr tesseract-ocr-eng`. The Docker image already
includes both the engine and its English language data.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

The web interface requires Node.js 20.9 or later. Run it alongside `ama-api`:

```bash
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:3000`. The Next.js server connects to the local API at
`http://127.0.0.1:8000` by default.

Copy `.env.example` to `.env` when you need to override the local defaults.
Configuration is read from environment variables; `.env` files are not loaded
automatically by the package and must be loaded by the chosen runtime. External
API keys are managed separately from the Settings page and persist in encrypted
local storage. `OPENAI_API_KEY` remains available as an environment fallback.

## Project layout

```text
src/maintenance_assistant/  Application code
web/                        Next.js worker interface
tests/                      Automated tests
docs/                       Architecture and engineering notes
data/                       Local runtime data (created when required)
```

The initial document-ingestion design is described in
[`docs/architecture.md`](docs/architecture.md).

## Ingest a document

After installing the project, ingest a local PDF, scanned image, text or
Markdown document:

```bash
ama-ingest /path/to/maintenance-manual.pdf
```

The application stores the original document, its metadata, token-bounded child
chunks and larger section context beneath `AMA_DATA_DIRECTORY` (`./data` by default). If
the same content is submitted again, the existing document is returned rather
than stored twice.

The command reports a stable error code and a concise explanation when a
document cannot be ingested. PDF pages without an embedded text layer and
`PNG`/`JPEG` document images are recognised locally with Tesseract. Digital PDF
pages retain their existing text, including within mixed digital/scanned PDFs.
Password-protected PDFs remain unsupported.

### Enable image and diagram understanding

Visual analysis is disabled until an OpenAI API key is available because
rendered document pages are sent to OpenAI. Add a key from **Settings → API
keys** (preferred) or provide the `OPENAI_API_KEY` environment fallback. This
also enables embeddings so descriptions of
equipment photographs, schematics, wiring and flow diagrams, drawings, charts
and tables become searchable evidence:

```bash
ama-ingest /path/to/maintenance-manual.pdf
```

Every PDF page is checked, including pages with digital text and scanned pages.
Only maintenance-relevant visual descriptions are added; text-only pages,
logos and decorative graphics are filtered. Descriptions retain their source
page and flow through the same chunking, embedding, hybrid retrieval and
grounded-citation path as extracted text. Existing manuals must be re-indexed
after enabling visual analysis.

The Manuals page retains revision history while ensuring only current manuals
contribute to search and answers. Workers can replace, archive, re-index or
permanently delete a manual through explicit lifecycle controls. Upload forms
can classify manuals with multiple brands, machines, sites/areas and document
types. Previously used values remain available in searchable tagging controls,
and the same classifications can restrict questions to matching current manuals.

### Enable embeddings and hybrid search

Embeddings are disabled until an OpenAI API key is available. Add one from the
Settings page before embedding new or previously ingested documents:

```bash
ama-ingest /path/to/maintenance-manual.pdf
```

When enabled, child chunk text and populated equipment metadata are sent to the
OpenAI Embeddings API. Original files, SQLite metadata and returned vectors
remain in the local data directory.

Search the embedded chunks with:

```bash
ama-search "How do I isolate the pump before maintenance?"
```

Search combines semantic vector similarity with SQLite full-text matches, so
natural-language questions and exact identifiers such as fault codes can both
influence the result order. The candidate count, RRF constant and method
weights are configurable through the `AMA_RETRIEVAL_*` settings in
`.env.example`.

An optional second-stage reranker can rescore the bounded hybrid candidate set
and remove evidence below a configured relevance threshold before it reaches
grounded answering. It fails safely back to fused retrieval order when the
provider is unavailable or returns invalid scores. See
[Retrieval reranking](docs/retrieval-reranking.md).

Retrieval changes can be measured against labelled source passages with the
local evaluation harness. A fictional starter corpus is included so the
workflow can be exercised without committing private manuals:

```bash
ama-evaluate-retrieval evals/retrieval-cases.json --limit 5
```

See [Retrieval evaluation](docs/retrieval-evaluation.md) for corpus setup,
metrics, JSON reports and quality gates.

The initial provider uses `text-embedding-3-small` with 512 dimensions. Both
values are configurable. Search must use the same provider configuration used
to create the stored vectors.

### Enable grounded answers

Grounded answers rank small child chunks, expand them to section-aligned parent
context and send only the question and selected context to OpenAI. Add the API
key in Settings; all fixed OpenAI-backed stages become available immediately,
without restarting the API:

```bash
ama-api
```

Then ask a question through the web interface, `POST /answers` or the
interactive `/docs` page.
Every supported claim uses a marker such as `[S1]`; each returned citation
contains the matching document, child retrieval anchor, parent evidence excerpt
and available page, heading or line range. The application refuses to return an answer when the
provider reports insufficient evidence or produces unverifiable citations.

### Continue earlier conversations

Every successful grounded-answer exchange is stored locally in SQLite as an
ordered user message and assistant response. The worker interface lists earlier
conversations, reopens their complete message and citation history, continues
the selected thread, starts a clean conversation or permanently deletes one.
Workers can rate each assistant response with thumbs up or down; the rating is
stored locally against that conversation message and can be changed or cleared.

Conversation history is retained by the same local data volume as manuals and
vectors. It is not automatically sent back to the answer model or used as
hidden retrieval context. See
[`docs/conversation-history.md`](docs/conversation-history.md) for the storage,
API, privacy and lifecycle design.

### Diagnose a fault

Open **Diagnose a fault** in the web interface and describe the symptom. Select
the affected equipment or manual when known and record whether the session is
limited to non-intrusive observation, has been confirmed safe for authorised
checks, or must stop and escalate.

Each response updates a durable investigation state containing known symptoms,
worker observations, measurements, completed checks and ranked possible causes.
Manual-derived checks and diagnostic findings retain verified citations. The
assistant can answer a follow-up such as “why does that matter?” before
continuing the investigation. See
[`docs/guided-diagnostics.md`](docs/guided-diagnostics.md).

## Run the application API

Start the local HTTP API after installing the project:

```bash
ama-api
```

The service listens on `http://127.0.0.1:8000` by default. Open
`http://127.0.0.1:8000/docs` for the interactive API documentation, or upload a
document directly:

```bash
curl -F "file=@/path/to/maintenance-manual.pdf" \
  http://127.0.0.1:8000/documents
```

Document upload, metadata browsing and health checks work without an external
key. Semantic search and grounded answers become available when the OpenAI key
is configured. The initial API
has no authentication and is intended for local development only; do not expose
it to an untrusted network.

## Run with Docker

Build the images and start the application with Docker Compose:

```bash
docker compose up --build --wait
```

The worker interface is available at `http://127.0.0.1:3000`. A dedicated
background worker processes persisted imports independently of the browser and
API request, while Qdrant provides indexed semantic candidate search with a
SQLite fallback. The API remains
available at `http://127.0.0.1:8000`, including its interactive documentation at
`/docs`. SQLite uses WAL for API/worker concurrency, while a persistent bounded
cache avoids recreating identical embeddings. Compose keeps documents, SQLite metadata, vectors and conversation
history in a named volume when the containers are recreated.
Guided diagnostic sessions and their evidence snapshots use the same volume.

Use a local `.env` file to change `AMA_API_PORT`; manage the OpenAI key from
Settings. Stop the
service without deleting its stored data with:

```bash
docker compose down
```

See [`docs/containerisation.md`](docs/containerisation.md) before deleting the
volume, which also contains the credential-encryption key.

See [`docs/indexed-vector-storage.md`](docs/indexed-vector-storage.md) for index
synchronisation, filtering, recovery and fallback behaviour.

See [`docs/document-ingestion.md`](docs/document-ingestion.md) for pipeline,
storage and limitation details. See
[`docs/visual-document-understanding.md`](docs/visual-document-understanding.md)
for visual enrichment, privacy, configuration and accuracy limits. See
[`docs/embeddings-and-vector-search.md`](docs/embeddings-and-vector-search.md)
for the embedding and retrieval design. The HTTP routes and examples are in
[`docs/application-api.md`](docs/application-api.md). Grounding, citation
validation and current limitations are described in
[`docs/grounded-answers.md`](docs/grounded-answers.md). See
[`docs/conversation-history.md`](docs/conversation-history.md) for durable local
message history. See
[`docs/app-managed-credentials.md`](docs/app-managed-credentials.md) for API-key
storage, precedence, recovery and security boundaries. See
[`docs/web-interface.md`](docs/web-interface.md) for the worker experience and
frontend architecture, and [`docs/manual-lifecycle.md`](docs/manual-lifecycle.md)
for revision, archive and deletion guarantees. Local concurrency, embedding
reuse and runtime measurements are covered in
[`docs/performance-and-caching.md`](docs/performance-and-caching.md).
