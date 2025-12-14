# Initial architecture

## Product boundary

AI Maintenance Assistant is a local-first application. Documents and extracted
content remain on the user's machine by default. External services must be
explicitly configured and their use made visible to the user.

Document ingestion turns supported local files into validated, traceable chunks.
Retrieval and grounded answering build on those chunks without weakening their
source traceability.

## Ingestion flow

```text
select document
      |
      v
validate file and type
      |
      v
extract text and source metadata
      |
      v
recognise textless pages locally when required
      |
      v
optionally describe maintenance visuals on every rendered page
      |
      v
normalise extracted content
      |
      v
split content into parent sections and retrieval children
      |
      v
optionally create embeddings
      |
      v
store original, metadata, chunks and vectors locally
```

Each stored parent and child retains enough source metadata to identify its
original document and location. Only children are embedded; a matched child
expands to its section parent for grounded-answer context. A failed stage should
return a clear error without
leaving a partially ingested document behind.

Each manual has a lifecycle state. A replacement is stored as a new immutable
revision and supersedes the previous current revision in the same transaction.
Retrieval joins the document record and accepts vectors from `current` manuals
only; superseded and archived revisions remain inspectable but cannot silently
contribute evidence.

## Grounded-answer flow

```text
question
   |
   v
embed query and search child vectors
   |
   v
deduplicate and label parent context S1, S2, ...
   |
   v
request a typed answer from the configured provider
   |
   v
verify answer markers and citation IDs against retrieved chunks
   |
   v
return answer, excerpts and traceable source locations
```

Retrieved document text is treated as untrusted evidence rather than model
instructions. An answer is returned only when its structured citation list and
inline markers agree and every identifier maps to a retrieved chunk. A missing,
duplicated or invented citation fails the request; insufficient evidence returns
a stable local refusal with no citations.

## Initial constraints

- PDF, PNG/JPEG image, plain-text and Markdown files are supported.
- The default maximum document size is 25 MB and can be configured.
- Source files are stored locally with metadata and chunks in SQLite.
- Embeddings are an explicit opt-in because chunk text leaves the local machine.
- Answer generation is a separate explicit opt-in because selected chunks and
  the user's question are sent to the configured provider.
- Visual analysis is a separate explicit opt-in because rendered PDF pages and
  uploaded document images are sent to the configured provider.
- Returned vectors are stored in SQLite and searched locally.
- Parsing, chunking and persistence remain separate components so they can be
  tested and replaced independently.
- Exact duplicates are detected with a SHA-256 content fingerprint.
- Revision replacement, archiving and permanent deletion are enforced by the
  storage boundary rather than relying on interface filtering.
- Tesseract OCR is local, bounded per page and applied only where a digital text
  layer is absent.

## Package boundaries

- `maintenance_assistant.config` owns runtime settings and validation.
- `maintenance_assistant.ingestion` will own the ingestion workflow and its
  domain types.
- `maintenance_assistant.ingestion.storage` owns the local SQLite database and
  controlled source-file copies, vectors and schema migrations.
- `maintenance_assistant.embeddings` owns the provider contract and real OpenAI
  Embeddings API implementation, plus the persistent cache wrapper.
- `maintenance_assistant.metrics` owns bounded in-process API timing aggregates
  without retaining request content.
- `maintenance_assistant.ocr` owns the local OCR contract and bounded Tesseract
  process integration.
- `maintenance_assistant.vision` owns typed visual-analysis results, image
  privacy controls and the OpenAI Responses API integration.
- `maintenance_assistant.retrieval` combines local vector and SQLite full-text
  rankings with weighted reciprocal rank fusion.
- `maintenance_assistant.reranking` optionally applies bounded, typed
  second-stage relevance scoring with deterministic retrieval fallback.
- `maintenance_assistant.answering` owns evidence labelling, the real OpenAI
  Responses API provider and citation validation.
- `maintenance_assistant.conversations` atomically stores complete worker and
  assistant exchanges, citation snapshots and conversation lifecycle operations.
- `maintenance_assistant.api` owns HTTP validation and response models while
  delegating ingestion, persistence and retrieval to the domain services.
- `maintenance_assistant.cli` provides the first runnable interface to the
  ingestion service.
- `web` owns the worker-facing Next.js application. Its server-side route
  handler proxies browser requests to FastAPI so the UI and API remain separate
  deployable processes without requiring browser cross-origin access.

These boundaries are intentionally small and may evolve as real pipeline
behaviour is implemented and tested.

## Container boundary

The API Docker image runs the same `ama-api` entry point as local development.
The web image runs the production Next.js standalone server. Compose connects
the web server to FastAPI on its internal network, sets the API data directory
to `/app/data` and mounts a named volume there so both processes remain
disposable while local documents and SQLite state persist.

Conversation history shares this SQLite database. Schema version 7 adds
`conversations` and ordered `conversation_messages`; deleting a conversation
cascades to its messages without affecting manuals or vectors.

The initial container is deliberately bound to the host loopback interface.
Containerisation does not add authentication or make the API safe to expose to
an untrusted network.
