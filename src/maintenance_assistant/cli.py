"""Command-line interface for local document ingestion."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import IngestionError, IngestionService, IngestionStatus


def main(argv: Sequence[str] | None = None) -> int:
    """Ingest one document and report a concise result."""

    parser = argparse.ArgumentParser(
        prog="ama-ingest",
        description="Ingest a maintenance document into local storage.",
    )
    parser.add_argument("document", type=Path, help="path to a PDF, text or Markdown file")
    arguments = parser.parse_args(argv)

    try:
        result = IngestionService(Settings.from_environment()).ingest(arguments.document)
    except (IngestionError, ValueError) as error:
        if isinstance(error, IngestionError):
            print(f"Ingestion failed [{error.code}]: {error.message}", file=sys.stderr)
        else:
            print(f"Configuration is invalid: {error}", file=sys.stderr)
        return 1

    if result.status is IngestionStatus.ALREADY_EXISTS:
        print(
            f"Document is already stored as {result.document.id} "
            f"({_chunk_label(result.document.chunk_count)})."
        )
    else:
        print(
            f"Ingested {result.document.original_filename} as {result.document.id} "
            f"({_chunk_label(result.document.chunk_count)})."
        )
    return 0


def _chunk_label(count: int) -> str:
    return f"{count} {'chunk' if count == 1 else 'chunks'}"
