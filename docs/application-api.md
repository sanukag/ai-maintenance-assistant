# Application API

The initial FastAPI application exposes the existing ingestion and retrieval
services over HTTP. It remains local-first: original documents, metadata,
chunks and vectors are stored beneath `AMA_DATA_DIRECTORY`.

## Start the service

Install the project and run:

```bash
ama-api
```

The defaults bind the service to `127.0.0.1:8000`. Alternative local values can
be supplied explicitly:

```bash
ama-api --host 127.0.0.1 --port 8080
```

Interactive OpenAPI documentation is available at `/docs`, with the raw schema
at `/openapi.json`.

The API reads the same environment variables as the command-line ingestion and
search tools. For example, start it with OpenAI embeddings enabled by exporting
`AMA_EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY` first. Grounded answers also
require `AMA_ANSWER_PROVIDER=openai`. Provider settings are fixed for the
lifetime of the process, so restart the API after changing them.

Set `AMA_VISUAL_ANALYSIS_PROVIDER=openai` to enrich uploaded images and every
rendered PDF page with maintenance-relevant visual meaning. This is independent
from OCR and requires `OPENAI_API_KEY`.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check storage, OCR, visual analysis and provider availability |
| `POST` | `/documents` | Upload and ingest one PDF, image, text or Markdown document |
| `GET` | `/documents` | List metadata with pagination and lifecycle filtering |
| `GET` | `/metadata/options` | List reusable equipment classifications for tagging controls |
| `GET` | `/documents/{document_id}` | Retrieve one document's metadata |
| `GET` | `/documents/{document_id}/revisions` | List retained revision history |
| `POST` | `/documents/{document_id}/revisions` | Install a replacement revision |
| `POST` | `/documents/{document_id}/reindex` | Refresh vectors for one manual |
| `POST` | `/documents/{document_id}/archive` | Exclude a retained manual from retrieval |
| `DELETE` | `/documents/{document_id}` | Permanently remove a manual and its data |
| `POST` | `/search` | Search embedded chunks and return source locations |
| `POST` | `/answers` | Generate a grounded answer with verified citations |
| `POST` | `/ingestion-jobs` | Persist an upload and queue background ingestion |
| `GET` | `/ingestion-jobs` | List recent queued, active and completed imports |
| `GET` | `/ingestion-jobs/{job_id}` | Poll progress and failure details |
| `POST` | `/ingestion-jobs/{job_id}/cancel` | Cancel queued work or request cooperative cancellation |
| `POST` | `/ingestion-jobs/{job_id}/retry` | Retry a retained failed or cancelled upload |
| `GET` | `/conversations` | List saved conversations from newest to oldest |
| `GET` | `/conversations/{conversation_id}` | Reopen every message and citation in a conversation |
| `PUT` | `/conversations/{conversation_id}/messages/{message_id}/feedback` | Record or replace answer feedback |
| `DELETE` | `/conversations/{conversation_id}/messages/{message_id}/feedback` | Clear answer feedback |
| `DELETE` | `/conversations/{conversation_id}` | Permanently delete a conversation and its messages |

Local storage paths and document content hashes are deliberately excluded from
responses.

## Upload a document

Send one multipart field named `file`. Optional `brand`, `machine`, `site` and
`document_type` fields classify the manual. Repeat a field to attach more than
one value in that category:

```bash
curl -F "file=@./manuals/pump.pdf" \
  -F "brand=Acme" -F "brand=Acme Industrial" \
  -F "machine=P-100" -F "machine=P-100 Mk II" -F "site=North plant" \
  -F "document_type=Service manual" \
  http://127.0.0.1:8000/ingestion-jobs
```

The API streams the upload into a temporary directory in 1 MB blocks and stops
when `AMA_MAX_DOCUMENT_SIZE_MB` is exceeded. It sanitises the supplied filename
before copying it into the durable job area. HTTP `202` is returned after that
copy and queue record commit. A dedicated worker then performs validation,
extraction, chunking, embedding and storage, updating the stage and percentage
between expensive steps. Completed staged files are removed; failed and
cancelled files are retained so an operator can retry them.

Scanned PDF pages and `PNG`/`JPEG` images are recognised with the configured
local Tesseract engine. OCR dependency failures return HTTP `503`, per-page
timeouts return HTTP `504`, and invalid or unrecognisable scans use stable
ingestion error codes.

When visual analysis is enabled, digital and scanned PDF pages plus uploaded
document images are analysed for equipment photographs, diagrams, drawings,
charts and tables. Provider failures return HTTP `502`, provider timeouts return
HTTP `504`, and unavailable configuration returns HTTP `503`. Successful visual
descriptions are stored as page-cited chunks and embedded with ordinary text.

A synchronous compatibility endpoint remains at `POST /documents`. A new
document there returns HTTP `201` and `status: completed`. Submitting identical
content returns HTTP `200`, `status: already_exists` and the existing document
identifier. If embeddings have since been enabled, the duplicate path also
backfills any missing vectors.

## Browse documents

```bash
curl "http://127.0.0.1:8000/documents?limit=20&offset=0"
curl "http://127.0.0.1:8000/documents?lifecycle_status=current"
curl http://127.0.0.1:8000/documents/<document-id>
```

Documents are returned newest first. The maximum page size is 100. The optional
`lifecycle_status` filter accepts `current`, `superseded` or `archived`.

## Manage manual revisions

Install a replacement by sending the same multipart `file` field to the current
manual's revisions endpoint:

```bash
curl -F "file=@./manuals/pump-revision-2.pdf" \
  http://127.0.0.1:8000/documents/<current-id>/revisions
```

The replacement receives the next revision number and becomes `current`; the
previous record becomes `superseded` in the same transaction. Both source files
and their extracted records remain available through:

```bash
curl http://127.0.0.1:8000/documents/<document-id>/revisions
```

Only current manuals contribute to search and grounded answers. Archive a
retained copy with `POST /documents/<id>/archive`. Regenerate a manual's
token-aware hierarchy, visual descriptions and vectors with the active
providers using `POST /documents/<id>/reindex`.

Permanent deletion uses `DELETE /documents/<id>` and removes the managed source
file, metadata, chunks and vectors. The worker interface requires an explicit
confirmation before sending this request.

## Search embedded chunks

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"How do I isolate the pump?","limit":5}'
```

Add `document_id`, `brand`, `machine`, `site` or `document_type` to restrict
results. Metadata fields accept either one string or an array. A document may
match any requested value within a category, while populated categories are
intersected case-insensitively with each other and the current-manual boundary.
Each result
contains its normalised hybrid score, raw semantic and lexical diagnostic
scores, contributing `retrieval_methods`, embedding model, safe document
metadata, child chunk, larger `parent_context` and available source location.
Search returns HTTP `503` with the `embeddings_disabled` code when semantic
retrieval is enabled but no embedding provider is configured.

## Ask a grounded question

```bash
curl -X POST http://127.0.0.1:8000/answers \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I isolate the pump?","max_sources":5}'
```

Add the same document or metadata criteria to restrict the evidence. The API
embeds the question, hybrid-ranks local child chunks, deduplicates shared parents and
labels up to `max_sources` contexts `S1`, `S2` and so on. The configured answer
model may use only those contexts. A successful response has this shape:

```json
{
  "conversation_id": "...",
  "question": "How do I isolate the pump?",
  "answerable": true,
  "answer": "Disconnect and lock out the supply [S1].",
  "citations": [
    {
      "source_id": "S1",
      "score": 0.94,
      "document": {"id": "...", "original_filename": "pump.pdf"},
      "chunk_id": "...",
      "chunk_sequence": 3,
      "parent_context_id": "...",
      "excerpt": "Disconnect and lock out the electrical supply...",
      "page_start": 8,
      "page_end": 8,
      "headings": ["Isolation"],
      "line_start": null,
      "line_end": null
    }
  ],
  "model": "gpt-5.6-terra",
  "usage": {"input_tokens": 412, "output_tokens": 54}
}
```

The example shortens the nested document metadata for readability; the real
response includes the same safe metadata returned by the document routes. The
answer service verifies that inline markers and structured citation IDs match
selected evidence before returning them. Insufficient evidence returns HTTP `200`
with `answerable: false`, a stable explanation, no citations and no invented
procedure. Provider failures or unverifiable output return HTTP `502`.

The returned `conversation_id` identifies the locally stored exchange. Supply
it with the next question to continue the same thread:

```bash
curl -X POST http://127.0.0.1:8000/answers \
  -H "Content-Type: application/json" \
  -d '{
    "question":"What should I inspect afterwards?",
    "conversation_id":"<conversation-id>"
  }'
```

`GET /conversations` lists saved threads with pagination, and
`GET /conversations/{conversation_id}` returns every ordered user and assistant
message with citation snapshots. `DELETE /conversations/{conversation_id}`
permanently removes that thread. Successful answers are stored atomically as a
pair, so provider failures do not leave incomplete conversations. A missing
conversation returns HTTP `404` before the answer provider is invoked.

`/answers` returns HTTP `503` with `answers_disabled` unless both an embedding
provider and an answer provider are enabled. The OpenAI answer provider defaults
to `gpt-5.6-terra`; `AMA_ANSWER_MODEL` and
`AMA_ANSWER_MAX_OUTPUT_TOKENS` are configurable.

## Error shape

Expected failures use stable codes:

```json
{
  "error": {
    "code": "unsupported_type",
    "message": "Unsupported document type: .csv"
  }
}
```

Request-schema failures use FastAPI's standard HTTP `422` validation response.

## Current boundary

This version is a local development API. It does not yet provide
authentication, authorisation, rate limiting, cross-origin policy or a remote
database. Keep it bound to the loopback interface. Grounding reduces unsupported
answers but is not a substitute for safety review of maintenance work.
