# AI Maintenance Assistant

[![CI](https://github.com/sanukag/ai-maintenance-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/sanukag/ai-maintenance-assistant/actions/workflows/ci.yml)

A local-first assistant for turning maintenance documents into useful,
traceable knowledge.

## Development setup

The project requires Python 3.12 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

Copy `.env.example` to `.env` when you need to override the local defaults.
Configuration is read from environment variables; `.env` files are not loaded
automatically by the package and must be loaded by the chosen runtime.

## Project layout

```text
src/maintenance_assistant/  Application code
tests/                      Automated tests
docs/                       Architecture and engineering notes
data/                       Local runtime data (created when required)
```

The initial document-ingestion design is described in
[`docs/architecture.md`](docs/architecture.md).

## Ingest a document

After installing the project, ingest a local PDF, text or Markdown document:

```bash
ama-ingest /path/to/maintenance-manual.pdf
```

The initial version stores the original document, its metadata and its
traceable text chunks beneath `AMA_DATA_DIRECTORY` (`./data` by default). If
the same content is submitted again, the existing document is returned rather
than stored twice.

The command reports a stable error code and a concise explanation when a
document cannot be ingested. Scanned PDFs requiring optical character
recognition and password-protected PDFs are not supported yet.

### Enable embeddings and semantic search

Embeddings are disabled by default. To embed new or previously ingested
documents with OpenAI:

```bash
export AMA_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=your-project-api-key
ama-ingest /path/to/maintenance-manual.pdf
```

When enabled, document chunk text is sent to the OpenAI Embeddings API. Original
files, metadata and returned vectors remain in the local data directory.

Search the embedded chunks with:

```bash
ama-search "How do I isolate the pump before maintenance?"
```

The initial provider uses `text-embedding-3-small` with 512 dimensions. Both
values are configurable. Search must use the same provider configuration used
to create the stored vectors.

### Enable grounded answers

Grounded answers retrieve evidence from the locally stored vectors and send
only the question and selected chunks to the configured answer provider. Enable
both OpenAI-backed stages before starting the API:

```bash
export AMA_EMBEDDING_PROVIDER=openai
export AMA_ANSWER_PROVIDER=openai
export OPENAI_API_KEY=your-project-api-key
ama-api
```

Then ask a question through `POST /answers` or the interactive `/docs` page.
Every supported claim uses a marker such as `[S1]`; each returned citation
contains the matching document, chunk, evidence excerpt and available page,
heading or line range. The application refuses to return an answer when the
provider reports insufficient evidence or produces unverifiable citations.

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

Document upload, metadata browsing and health checks work with the default
local-only provider configuration. Semantic search requires embeddings;
grounded answers require both embedding and answer providers. The initial API
has no authentication and is intended for local development only; do not expose
it to an untrusted network.

## Run with Docker

Build the image and start the API with Docker Compose:

```bash
docker compose up --build --wait
```

The API is available at `http://127.0.0.1:8000`, including its interactive
documentation at `/docs`. Compose keeps documents, SQLite metadata and vectors
in a named volume when the container is recreated.

Use a local `.env` file to change `AMA_API_PORT` or enable embeddings. Stop the
service without deleting its stored data with:

```bash
docker compose down
```

See [`docs/containerisation.md`](docs/containerisation.md) before deleting the
volume or enabling an external embedding provider.

See [`docs/document-ingestion.md`](docs/document-ingestion.md) for pipeline,
storage and limitation details. See
[`docs/embeddings-and-vector-search.md`](docs/embeddings-and-vector-search.md)
for the embedding and retrieval design. The HTTP routes and examples are in
[`docs/application-api.md`](docs/application-api.md). Grounding, citation
validation and current limitations are described in
[`docs/grounded-answers.md`](docs/grounded-answers.md).
