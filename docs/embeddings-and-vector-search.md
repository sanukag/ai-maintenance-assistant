# Embeddings and hybrid search

## Purpose

Embeddings convert document chunks into numerical vectors. Chunks with similar
meaning should have nearby vectors, allowing retrieval to find relevant source
material even when a query uses different wording. SQLite full-text search
complements those vectors by preserving exact terms such as fault codes, part
numbers and component names.

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

1. The normal ingestion stages create traceable parent sections and smaller
   child chunks.
2. The provider creates one vector per child chunk.
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

## Hybrid search

Run:

```bash
ama-search "pump seal replacement interval"
```

The default search path:

1. Creates an embedding for the query with the configured provider.
2. Ranks matching local vectors by cosine similarity.
3. Independently ranks exact text matches with SQLite FTS5 and BM25.
4. Combines the two ordered candidate lists with weighted reciprocal rank
   fusion (RRF).
5. Returns the highest-scoring children with filename, source location and their
   larger parent context.

The public `score` is the fused RRF score normalised to a maximum of `1`. The
API also exposes the raw `semantic_score`, `lexical_score` and
`retrieval_methods` for diagnosis. Raw scores from the two retrieval methods
are not added together because they have different scales.

The child remains the precise retrieval anchor. Grounded answering deduplicates
children belonging to the same parent and sends the larger parent section as
evidence, giving the model procedural context without making search chunks less
focused. Legacy chunks without a parent remain searchable and use their own text
as context until the manual is re-indexed.

Optional arguments:

```bash
ama-search "pump isolation" --limit 3
ama-search "pump isolation" --document-id <document-id>
```

Hybrid retrieval is configurable:

```text
AMA_RETRIEVAL_CANDIDATE_LIMIT=30
AMA_RETRIEVAL_RRF_K=60
AMA_RETRIEVAL_SEMANTIC_WEIGHT=1
AMA_RETRIEVAL_TEXT_WEIGHT=1
```

The candidate limit controls how many results each method contributes before
fusion. The two weights can favour semantic or exact-text ranking, but they
cannot both be zero. For the initial single-user corpus, application-level
cosine ranking plus SQLite FTS5 keeps the runtime local and inspectable. A
dedicated search service should be considered when corpus size makes loading
matching vectors too slow or memory-intensive.

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
