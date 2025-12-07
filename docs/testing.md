# Testing strategy

The project tests ingestion at three levels:

- Unit tests exercise validation, extraction, normalisation, chunk boundaries,
  configuration and storage rollback behaviour.
- Integration tests pass real temporary files through SQLite and controlled
  local file storage.
- End-to-end tests ingest text, Markdown and a generated real PDF through the
  same service used by the command-line interface.

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
