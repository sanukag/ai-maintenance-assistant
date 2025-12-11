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
- Frontend component tests exercise the worker question flow, verified source
  presentation, manual upload, lifecycle confirmations, revision installation,
  library status and the developer settings page with deterministic responses.
- Lifecycle tests verify the schema migration, atomic superseding, active-only
  retrieval, archive exclusion, cascading deletion and retained history.
- Retrieval-evaluation tests verify dataset validation, labelled passage
  matching, ranking metrics, score-threshold experiments, JSON reports and
  command-line quality gates using deterministic local vectors.

Run the complete suite with:

```bash
pytest
```

Run it with branch coverage reporting when reviewing a larger change:

```bash
pytest --cov=maintenance_assistant --cov-report=term-missing
```

Run the web checks from `web/`:

```bash
npm test
npm run lint
npm run build
npm audit --omit=dev
```

Tests use isolated temporary directories and must not read or write the normal
`./data` directory. New parsers should include malformed, empty and successful
fixtures as well as one end-to-end ingestion case.

Automated tests inject deterministic vectors and never require an OpenAI API
key. They also inject deterministic answer payloads, so CI never sends questions
or chunks to an external model. A live-provider smoke test should be run
deliberately with a project key before release because it sends content
externally and incurs API usage.

The separate [retrieval evaluation workflow](retrieval-evaluation.md) measures
quality against labelled maintenance questions. Its committed starter corpus is
synthetic; a production-quality gate should use a larger reviewed dataset kept
within the appropriate privacy boundary.

## Container verification

When the Docker CLI is installed, the normal suite validates the resolved
Compose model without starting a container. Run the complete Docker integration
test deliberately with:

```bash
AMA_RUN_CONTAINER_TESTS=1 pytest tests/container -q
```

It builds both images, waits for the API and web health checks, confirms both
processes use the non-root UID, verifies the internal API proxy, installs a real
replacement revision, restarts the API, checks volume persistence and exercises
archive and deletion through the web proxy. The isolated test Compose project,
images and volume are removed in cleanup.

## Continuous integration

GitHub Actions runs three independent jobs for pull requests targeting `main`
and for changes merged into `main`:

- `Web checks` installs the locked Node.js dependencies, audits production
  packages, runs component tests and linting, then produces a Next.js build;
- `Python tests` installs Python 3.12, validates the Compose model, runs the
  complete normal suite and rejects coverage below 90%;
- `Container runtime` builds the real API and web images and verifies health,
  internal proxying, non-root execution, upload handling, restart behaviour and
  volume persistence.

The workflow uses read-only repository permissions, immutable action commit
references and concurrency cancellation. It does not receive an OpenAI API key;
provider tests use deterministic local doubles.

The local equivalents are:

```bash
docker compose config --quiet
pytest --cov=maintenance_assistant --cov-branch --cov-fail-under=90 -q
AMA_RUN_CONTAINER_TESTS=1 pytest tests/container -q
```
