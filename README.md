# AI Maintenance Assistant

A local-first assistant for turning maintenance documents into useful,
traceable knowledge.

## Development setup

The project requires Python 3.12 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

Copy `.env.example` to `.env` when you need to override the local defaults.
Configuration is read from environment variables; `.env` files are not loaded
automatically by the package and must be loaded by the chosen runtime.

## Project layout

```text
src/maintenance_assistant/  Application code
tests/                      Automated tests
docs/                       Architecture and engineering notes
data/                       Local runtime data (created when required)
```

The initial document-ingestion design is described in
[`docs/architecture.md`](docs/architecture.md).

## Ingest a document

After installing the project, ingest a local PDF, text or Markdown document:

```bash
ama-ingest /path/to/maintenance-manual.pdf
```

The initial version stores the original document, its metadata and its
traceable text chunks beneath `AMA_DATA_DIRECTORY` (`./data` by default). If
the same content is submitted again, the existing document is returned rather
than stored twice.

The command reports a stable error code and a concise explanation when a
document cannot be ingested. Scanned PDFs requiring optical character
recognition and password-protected PDFs are not supported yet.

See [`docs/document-ingestion.md`](docs/document-ingestion.md) for pipeline,
storage and limitation details.
