# Indexed vector storage

SQLite remains the durable source of truth for manuals, chunks, metadata and
embedding vectors. Docker Compose also runs Qdrant 1.18.2 and configures the API
and ingestion worker to use its cosine HNSW index for semantic candidate
retrieval.

## Data flow

After SQLite commits an ingestion, revision, metadata edit or re-index, the
same document's active vectors and filter payload are upserted into Qdrant.
Archiving, superseding and deleting a manual remove its points. Each point uses
the stable SQLite chunk UUID and carries only retrieval metadata: document ID,
lifecycle state, embedding model and dimensions, brand, machine, site and
document type. Manual text and original files remain in SQLite-managed storage.

At startup, the ingestion worker rebuilds the external index from SQLite. This
makes existing manuals available after enabling Qdrant and repairs incomplete
writes after an outage. `POST /vector-index/rebuild` provides the same recovery
operation on demand.

## Failure behaviour

Qdrant is an acceleration layer, not the authoritative database. An index write
failure is logged after the SQLite transaction succeeds. If indexed search is
unavailable or returns no candidates, retrieval falls back to the existing
SQLite cosine scan. Indexed IDs are always hydrated back through SQLite, which
also rechecks that their manual is current.

The health endpoint reports `vector_store`, plus `vector_index` as `available`,
`unavailable` or `disabled`. The Settings page presents the same state to an
operator.

## Configuration

| Variable | Compose default | Purpose |
| --- | --- | --- |
| `AMA_VECTOR_STORE` | `qdrant` | Use `qdrant` or the SQLite-only fallback |
| `AMA_QDRANT_URL` | `http://qdrant:6333` | Internal API URL; fixed by Compose |
| `AMA_QDRANT_TIMEOUT_SECONDS` | `5` | Bounded external-index request timeout |
| `AMA_QDRANT_PORT` | `6333` | Loopback-only host port for local diagnostics |

The Qdrant data directory uses its own named volume. `docker compose down`
preserves it; `docker compose down --volumes` deliberately removes both the
Qdrant index and authoritative application volume.
