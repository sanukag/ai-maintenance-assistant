# Embeddings and vector search

## Purpose

Embeddings convert document chunks into numerical vectors. Chunks with similar
meaning should have nearby vectors, allowing retrieval to find relevant source
material even when a query uses different wording.

The first production provider uses OpenAI's
[`text-embedding-3-small`](https://developers.openai.com/api/docs/models/text-embedding-3-small)
model through the
[`/v1/embeddings`](https://developers.openai.com/api/reference/resources/embeddings)
endpoint. The provider sends inputs in bounded batches and restores the API
response to input order before storage.

## Privacy boundary

Embedding is disabled by default:

```text
AMA_EMBEDDING_PROVIDER=none
```

Enabling OpenAI embedding sends normalised chunk text and semantic search
queries to the OpenAI API. It does not upload the original document file.
Original files, document metadata, chunks and returned vectors remain in the
configured local data directory.

Enable it explicitly:

```bash
export AMA_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=your-project-api-key
```

Do not commit an API key to Git or place it in `.env.example`.

## Ingestion behaviour

For a new document with embedding enabled:

1. The normal ingestion stages create traceable chunks.
2. The provider creates one vector per chunk.
3. The pipeline verifies the response count, model, dimensions and finite
   values.
4. The document, chunks and vectors are committed to SQLite together.

If embedding fails, a new document is not stored as successfully ingested.

When an existing document is ingested again, the pipeline checks for vectors
matching the configured model and dimensions. It embeds and saves only missing
chunks. Existing matching vectors do not trigger another API request.

## Local vector storage

SQLite schema version 2 adds an `embeddings` table keyed by:

- chunk identifier
- embedding model
- vector dimensions

Vectors are stored as compact little-endian float32 blobs. Their magnitude is
stored alongside them for cosine similarity. Multiple model or dimension
configurations can coexist for the same chunk, which supports controlled
re-embedding later.

Existing schema-version-1 databases are migrated automatically without
changing their documents or chunks.

## Semantic search

Run:

```bash
ama-search "pump seal replacement interval"
```

The search path:

1. Creates an embedding for the query with the configured provider.
2. Loads local vectors with the same model and dimensions.
3. Calculates cosine similarity locally.
4. Returns the highest-scoring chunks with filename and source location.

Optional arguments:

```bash
ama-search "pump isolation" --limit 3
ama-search "pump isolation" --document-id <document-id>
```

For the initial single-user corpus, application-level cosine ranking keeps the
runtime simple and inspectable. A dedicated vector extension or database should
be considered when corpus size makes loading matching vectors too slow or
memory-intensive.

## Model and dimensions

Defaults:

```text
AMA_EMBEDDING_MODEL=text-embedding-3-small
AMA_EMBEDDING_DIMENSIONS=512
```

The dimensions parameter is supported by `text-embedding-3` models. Reducing
dimensions lowers local storage and comparison cost, although retrieval quality
must be evaluated with representative maintenance documents before treating
512 as a final production choice.

Changing the model or dimensions does not overwrite older vectors. Re-ingesting
a document creates the missing configuration, and searches use only vectors
matching the current configuration.
