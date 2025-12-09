# Testing strategy

The project tests ingestion at three levels:

- Unit tests exercise validation, extraction, normalisation, chunk boundaries,
  configuration and storage rollback behaviour.
- Integration tests pass real temporary files through SQLite and controlled
  local file storage.
- End-to-end tests ingest text, Markdown and a generated real PDF through the
  same service used by the command-line interface.
- Provider-boundary tests verify OpenAI request batching, input ordering, API
  failures, dimensions and non-finite vectors without making paid API calls.
- Vector tests cover schema-version migration, float storage, missing-vector
  backfill, cosine ranking and semantic-search output.
- API integration tests exercise health reporting, bounded multipart uploads,
  duplicate handling, document browsing, structured errors, OpenAPI generation
  and semantic search through the real local services.
- Grounded-answer tests exercise evidence retrieval, source labelling, typed
  Responses API calls, insufficient-evidence handling and rejection of missing,
  duplicated, mismatched or invented citations.

Run the complete suite with:

```bash
pytest
```

Run it with branch coverage reporting when reviewing a larger change:

```bash
pytest --cov=maintenance_assistant --cov-report=term-missing
```

Tests use isolated temporary directories and must not read or write the normal
`./data` directory. New parsers should include malformed, empty and successful
fixtures as well as one end-to-end ingestion case.

Automated tests inject deterministic vectors and never require an OpenAI API
key. They also inject deterministic answer payloads, so CI never sends questions
or chunks to an external model. A live-provider smoke test should be run
deliberately with a project key before release because it sends content
externally and incurs API usage.

## Container verification

When the Docker CLI is installed, the normal suite validates the resolved
Compose model without starting a container. Run the complete Docker integration
test deliberately with:

```bash
AMA_RUN_CONTAINER_TESTS=1 pytest tests/container -q
```

It builds the image, waits for the API health check, confirms the process uses
the non-root UID, uploads a real text document, restarts the container and
checks that the named volume preserved the document. The isolated test Compose
project, image and volume are removed in cleanup.

## Continuous integration

GitHub Actions runs two independent jobs for pull requests targeting `main`
and for changes merged into `main`:

- `Python tests` installs Python 3.12, validates the Compose model, runs the
  complete normal suite and rejects coverage below 90%;
- `Container runtime` builds the real image and verifies health, non-root
  execution, upload handling, restart behaviour and volume persistence.

The workflow uses read-only repository permissions, immutable action commit
references and concurrency cancellation. It does not receive an OpenAI API key;
provider tests use deterministic local doubles.

The local equivalents are:

```bash
docker compose config --quiet
pytest --cov=maintenance_assistant --cov-branch --cov-fail-under=90 -q
AMA_RUN_CONTAINER_TESTS=1 pytest tests/container -q
```
