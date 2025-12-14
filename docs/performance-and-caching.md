# Local performance and caching

The API and ingestion worker share one SQLite database. SQLite runs in
write-ahead logging (`WAL`) mode with normal synchronous durability and a
configurable busy timeout. WAL allows readers to continue while the worker is
writing; the timeout gives short write conflicts time to resolve instead of
failing immediately.

```text
AMA_SQLITE_BUSY_TIMEOUT_MS=5000
```

## Embedding cache

Schema version 12 adds a persistent, bounded embedding cache. Its key is a
SHA-256 digest of the provider model, vector dimensions and complete input
text. The database stores the digest and vector, not another plaintext copy of
the chunk or question.

The cache is shared by synchronous ingestion, background ingestion, CLI tools
and retrieval. Repeated text is deduplicated within one request, and successful
vectors can be reused after process or container restarts. Model or dimension
changes produce different keys, preventing incompatible vectors from being
reused.

```text
AMA_EMBEDDING_CACHE_MAX_ENTRIES=10000
```

The oldest unused entries are removed when the bound is exceeded. Set the value
to `0` to disable the wrapper. Provider token counts report only uncached work.
Generated answers are not cached because manual revisions, lifecycle changes
and retrieval thresholds could otherwise make a previously safe response
stale.

## Runtime measurements

`GET /metrics` returns process uptime, aggregate request counts, server errors,
average and maximum duration per templated route, embedding-cache entries and
hits, and the effective SQLite journal and timeout settings. It never stores or
returns questions, document content, identifiers, headers or client addresses.

The Settings page shows the most useful local values. Measurements reset when
the API restarts; persistent cache counts remain in SQLite. The endpoint is
intended for local diagnosis and does not replace authenticated production
observability if the application is ever exposed beyond one trusted machine.
