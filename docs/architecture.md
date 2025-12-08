# Initial architecture

## Product boundary

AI Maintenance Assistant is a local-first application. Documents and extracted
content remain on the user's machine by default. External services must be
explicitly configured and their use made visible to the user.

The first product capability will be document ingestion. Its responsibility is
to turn a supported local document into validated, traceable chunks that later
retrieval and assistant features can use.

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

## Initial constraints

- PDF, plain-text and Markdown files are the intended initial formats.
- The default maximum document size is 25 MB and can be configured.
- Source files are stored locally with metadata and chunks in SQLite.
- Embeddings are an explicit opt-in because chunk text leaves the local machine.
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
- `maintenance_assistant.api` owns HTTP validation and response models while
  delegating ingestion, persistence and retrieval to the domain services.
- `maintenance_assistant.cli` provides the first runnable interface to the
  ingestion service.

These boundaries are intentionally small and may evolve as real pipeline
behaviour is implemented and tested.
