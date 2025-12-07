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
store document, chunks and ingestion result locally
```

Each stored chunk should retain enough source metadata to identify its original
document and location. A failed stage should return a clear error without
leaving a partially ingested document behind.

## Initial constraints

- PDF, plain-text and Markdown files are the intended initial formats.
- The default maximum document size is 25 MB and can be configured.
- Storage is local; its implementation will be chosen with the ingestion work.
- Parsing, chunking and persistence remain separate components so they can be
  tested and replaced independently.
- Duplicate handling, scanned-PDF optical character recognition and additional
  formats will be decided during ingestion design rather than assumed here.

## Package boundaries

- `maintenance_assistant.config` owns runtime settings and validation.
- `maintenance_assistant.ingestion` will own the ingestion workflow and its
  domain types.
- Storage adapters will sit behind an interface defined by the needs of the
  ingestion workflow.

These boundaries are intentionally small and may evolve as real pipeline
behaviour is implemented and tested.
