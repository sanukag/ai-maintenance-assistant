# Containerisation

The Docker setup packages the FastAPI application and worker-facing Next.js
interface without changing ingestion, embedding or storage behaviour. Docker
Compose provides their local network, runtime configuration and persistent data
volume.

The API image also installs Tesseract with English language data. OCR remains
fully local and is used only for PDF pages without text and for image manuals.

## Start the application

From the repository root, run:

```bash
docker compose up --build --wait
```

Compose builds both images, starts the services and waits until both health
checks report success. The worker interface is available at:

- `http://127.0.0.1:3000`

The API and developer documentation remain available at:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

Follow the service logs with:

```bash
docker compose logs --follow api
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

Embeddings and grounded answers remain disabled by default. To enable both
OpenAI providers, set these values only in the untracked `.env` file:

```env
AMA_EMBEDDING_PROVIDER=openai
AMA_ANSWER_PROVIDER=openai
OPENAI_API_KEY=your-project-api-key
```

`AMA_ANSWER_MODEL` defaults to `gpt-5.6-terra`, and
`AMA_ANSWER_MAX_OUTPUT_TOKENS` defaults to `1000`. The same runtime key is used
for embeddings and answers. Restart or recreate the service after changing
provider settings:

```bash
docker compose up --build --detach --wait
```

Do not add a real key to `.env.example`, the Dockerfile or the image. Compose
passes the key at runtime; it is not required while building the image.

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
- extracted chunks and stored vectors.

Answers are generated per request and are not persisted in this initial
version.

Rebuilding the image, restarting the service or running the following command
does not delete the volume:

```bash
docker compose down
```

To deliberately remove all container-managed application data:

```bash
docker compose down --volumes
```

That deletion is irreversible unless the volume has been backed up.

## Runtime safeguards

The initial Compose service:

- binds the host port only to `127.0.0.1`;
- runs as the fixed non-root UID `10001`;
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

The integration test builds both images, waits for both health checks, confirms
both processes use UID `10001`, verifies the web-to-API proxy, uploads a real
document, restarts the API and checks the persistent volume.
