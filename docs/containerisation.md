# Containerisation

The Docker setup packages the FastAPI application and worker-facing Next.js
interface together with a dedicated ingestion worker and Qdrant vector index. Docker Compose provides
their local network, runtime configuration and persistent data volume. The API
persists each upload before returning, while the worker performs OCR, visual
analysis, chunking, embedding and storage in the background.

The API image also installs Tesseract with English language data. OCR remains
fully local and is used only for PDF pages without text and for image manuals.

## Start the application

From the repository root, run:

```bash
docker compose up --build --wait
```

Compose builds the application images, starts all four services and waits until
their health checks report success. The worker interface is available at:

- `http://127.0.0.1:3000`

The API and developer documentation remain available at:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/metrics`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`
- `http://127.0.0.1:6333/dashboard` for local Qdrant diagnostics

Follow API and ingestion-worker logs with:

```bash
docker compose logs --follow api worker
```

Follow the interface logs with `docker compose logs --follow web`.

## Configuration

Docker Compose automatically reads a `.env` file in the repository root. Copy
the example before changing runtime values:

```bash
cp .env.example .env
```

`AMA_API_PORT` controls the host port. The API always listens on port 8000
inside the container. For example:

```env
AMA_API_PORT=8080
```

The service would then be available at `http://127.0.0.1:8080`.

`AMA_WEB_PORT` controls the interface port and defaults to `3000`. The Next.js
server reaches FastAPI through `AMA_API_BASE_URL`; Compose fixes that value to
the internal `http://api:8000` service address.

`AMA_QDRANT_PORT` controls the loopback-only diagnostic port and defaults to
`6333`. Application containers use the internal `http://qdrant:6333` address.
Set `AMA_VECTOR_STORE=sqlite` to bypass Qdrant without removing its stored data.
`AMA_SQLITE_BUSY_TIMEOUT_MS` bounds API/worker lock waits, and
`AMA_EMBEDDING_CACHE_MAX_ENTRIES` bounds persistent vector reuse. Both services
receive the same values through Compose.

Embeddings, reranking, visual analysis and grounded answers remain disabled
until an OpenAI API key is available. Prefer adding it from the Settings page.
It is encrypted in the shared application-data volume, takes effect immediately
and survives container recreation.

Image and diagram understanding is also disabled by default because it sends a
rendered image of every PDF page, or an uploaded PNG/JPEG document, to OpenAI.
Adding the OpenAI key also enables the complete visual retrieval path.

`AMA_VISUAL_ANALYSIS_DETAIL=high` provides bounded high-fidelity processing for
diagram labels. The page count, render resolution, rendered-pixel limit, request
timeout and output-token limit are configurable through `.env.example`.

`AMA_ANSWER_MODEL` defaults to `gpt-5.6-terra`, and
`AMA_ANSWER_MAX_OUTPUT_TOKENS` defaults to `1000`. The same runtime key is used
for visual analysis, embeddings, reranking and answers. Model settings remain
environment-managed and require a service restart after changes. As a fallback,
`OPENAI_API_KEY` may be placed in the untracked `.env` file before starting
Compose. A value saved in Settings takes precedence. Do not add a real key to
`.env.example`, the Dockerfile or the image.

Set `AMA_OCR_LANGUAGE` to additional installed Tesseract language codes joined
with `+`. Additional language packages must also be added to the API image;
English (`eng`) is the included default.

`AMA_DATA_DIRECTORY` is fixed to `/app/data` by Compose because that path is
backed by the persistent volume. The local non-container command still uses
the value from `.env.example` when the environment is loaded explicitly.

## Persistence

The `maintenance-data` named volume contains:

- the SQLite database;
- managed copies of ingested documents;
- queued and failed uploads retained for recovery or retry;
- extracted chunks and stored vectors;
- complete conversation and citation history;
- guided diagnostic state, turn history and evidence snapshots;
- encrypted external credentials; and
- the owner-readable credential-encryption key.

The separate `qdrant-data` volume contains the rebuildable HNSW index. SQLite
keeps the authoritative vector copy, so losing only the Qdrant volume does not
lose manuals and the worker rebuilds the index when it starts.

Successful answers and their user questions are retained as atomic message
pairs. Provider failures are not stored as incomplete exchanges.

Rebuilding the image, restarting the service or running the following command
does not delete the volume:

```bash
docker compose down
```

To deliberately remove all container-managed application data:

```bash
docker compose down --volumes
```

That deletion is irreversible unless the volume has been backed up. Back up
the SQLite database and `credential-encryption.key` together; either part alone
cannot restore a saved API key.

## Runtime safeguards

The API, ingestion-worker and Qdrant services:

- bind any host port only to `127.0.0.1`;
- run as non-root users;
- drops Linux capabilities and prevents privilege escalation;
- uses a read-only root filesystem;
- provides a bounded temporary filesystem for uploads;
- uses an HTTP health check against the real application and storage path.

The web container follows the same non-root, read-only, capability-dropping and
loopback-binding approach. It exposes no provider credentials to the browser;
its route handler forwards only application requests to FastAPI.

These controls reduce the local container's privileges. They do not provide
API authentication, authorisation or rate limiting, so the service should not
be published directly to an external network.

## Verification

Validate the Compose model without starting the service:

```bash
docker compose config --quiet
```

Run the full image and persistence test when Docker is available:

```bash
AMA_RUN_CONTAINER_TESTS=1 pytest tests/container -q
```

The integration test builds the application images, waits for every health
check, confirms non-root processes, verifies the web-to-API proxy, uploads a
real document, restarts the API, verifies an encrypted credential survives and
checks persistent volumes.
