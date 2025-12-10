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

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check local storage and report embedding availability |
| `POST` | `/documents` | Upload and ingest one PDF, text or Markdown document |
| `GET` | `/documents` | List document metadata with `limit` and `offset` |
| `GET` | `/documents/{document_id}` | Retrieve one document's metadata |
| `POST` | `/search` | Search embedded chunks and return source locations |
| `POST` | `/answers` | Generate a grounded answer with verified citations |

Local storage paths and document content hashes are deliberately excluded from
responses.

## Upload a document

Send one multipart field named `file`:

```bash
curl -F "file=@./manuals/pump.pdf" http://127.0.0.1:8000/documents
```

The API streams the upload into a temporary directory in 1 MB blocks and stops
when `AMA_MAX_DOCUMENT_SIZE_MB` is exceeded. It sanitises the supplied filename
before passing the temporary file into the normal validation, extraction,
chunking, embedding and storage workflow. The temporary copy is removed after
the request; the ingestion store retains its own managed original.

A new document returns HTTP `201` and `status: completed`. Submitting identical
content returns HTTP `200`, `status: already_exists` and the existing document
identifier. If embeddings have since been enabled, the duplicate path also
backfills any missing vectors.

## Browse documents

```bash
curl "http://127.0.0.1:8000/documents?limit=20&offset=0"
curl http://127.0.0.1:8000/documents/<document-id>
```

Documents are returned newest first. The maximum page size is 100.

## Search embedded chunks

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"How do I isolate the pump?","limit":5}'
```

Add `document_id` to restrict results to one stored document. Each result
contains its cosine-similarity score, embedding model, safe document metadata,
chunk text and available page, heading or line location. Search returns HTTP
`503` with the `embeddings_disabled` code when no provider is configured.

## Ask a grounded question

```bash
curl -X POST http://127.0.0.1:8000/answers \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I isolate the pump?","max_sources":5}'
```

Add `document_id` to restrict the evidence to one stored document. The API
embeds the question, retrieves up to `max_sources` local chunks, labels them
`S1`, `S2` and so on, and asks the configured answer model to use only that
evidence. A successful response has this shape:

```json
{
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
retrieved chunks before returning them. Insufficient evidence returns HTTP `200`
with `answerable: false`, a stable explanation, no citations and no invented
procedure. Provider failures or unverifiable output return HTTP `502`.

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
