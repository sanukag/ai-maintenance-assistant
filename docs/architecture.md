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
normalise extracted content
      |
      v
split content into traceable chunks
      |
      v
optionally create embeddings
      |
      v
store original, metadata, chunks and vectors locally
```

Each stored chunk should retain enough source metadata to identify its original
document and location. A failed stage should return a clear error without
leaving a partially ingested document behind.

## Grounded-answer flow

```text
question
   |
   v
embed query and search local vectors
   |
   v
label retrieved chunks S1, S2, ...
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

- PDF, plain-text and Markdown files are the intended initial formats.
- The default maximum document size is 25 MB and can be configured.
- Source files are stored locally with metadata and chunks in SQLite.
- Embeddings are an explicit opt-in because chunk text leaves the local machine.
- Answer generation is a separate explicit opt-in because selected chunks and
  the user's question are sent to the configured provider.
- Returned vectors are stored in SQLite and searched locally.
- Parsing, chunking and persistence remain separate components so they can be
  tested and replaced independently.
- Exact duplicates are detected with a SHA-256 content fingerprint.
- Scanned-PDF optical character recognition and additional formats are
  deliberately outside the initial version.

## Package boundaries

- `maintenance_assistant.config` owns runtime settings and validation.
- `maintenance_assistant.ingestion` will own the ingestion workflow and its
  domain types.
- `maintenance_assistant.ingestion.storage` owns the local SQLite database and
  controlled source-file copies, vectors and schema migrations.
- `maintenance_assistant.embeddings` owns the provider contract and real OpenAI
  Embeddings API implementation.
- `maintenance_assistant.retrieval` embeds a query and searches local vectors.
- `maintenance_assistant.answering` owns evidence labelling, the real OpenAI
  Responses API provider and citation validation.
- `maintenance_assistant.api` owns HTTP validation and response models while
  delegating ingestion, persistence and retrieval to the domain services.
- `maintenance_assistant.cli` provides the first runnable interface to the
  ingestion service.

These boundaries are intentionally small and may evolve as real pipeline
behaviour is implemented and tested.

## Container boundary

The Docker image runs the same `ama-api` entry point as local development. It
does not introduce a second application configuration or storage path. Compose
sets the in-container data directory to `/app/data` and mounts a named volume
there so the API process remains disposable while local documents and SQLite
state persist.

The initial container is deliberately bound to the host loopback interface.
Containerisation does not add authentication or make the API safe to expose to
an untrusted network.
