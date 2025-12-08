"""Command-line interface for local document ingestion."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import create_embedding_provider
from maintenance_assistant.ingestion import (
    IngestionError,
    IngestionService,
    IngestionStatus,
    VectorSearchResult,
)
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.retrieval import VectorSearchService


def main(argv: Sequence[str] | None = None) -> int:
    """Ingest one document and report a concise result."""

    parser = argparse.ArgumentParser(
        prog="ama-ingest",
        description="Ingest a maintenance document into local storage.",
    )
    parser.add_argument("document", type=Path, help="path to a PDF, text or Markdown file")
    arguments = parser.parse_args(argv)

    try:
        settings = Settings.from_environment()
        provider = create_embedding_provider(settings)
        result = IngestionService(
            settings,
            embedding_provider=provider,
        ).ingest(arguments.document)
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
    if result.embedding_model:
        print(
            f"Stored {_vector_label(result.embedded_chunk_count)} using "
            f"{result.embedding_model}."
        )
    else:
        print("Embeddings are disabled; only document chunks were stored.")
    return 0


def _chunk_label(count: int) -> str:
    return f"{count} {'chunk' if count == 1 else 'chunks'}"


def _vector_label(count: int) -> str:
    return f"{count} {'vector' if count == 1 else 'vectors'}"


def search_main(argv: Sequence[str] | None = None) -> int:
    """Run semantic search over locally stored document chunks."""

    parser = argparse.ArgumentParser(
        prog="ama-search",
        description="Search embedded maintenance documents.",
    )
    parser.add_argument("query", help="maintenance question or search phrase")
    parser.add_argument("--limit", type=int, default=5, help="maximum results")
    parser.add_argument("--document-id", help="restrict search to one document")
    arguments = parser.parse_args(argv)

    try:
        settings = Settings.from_environment()
        provider = create_embedding_provider(settings)
        if provider is None:
            raise ValueError(
                "semantic search requires AMA_EMBEDDING_PROVIDER=openai"
            )
        results = VectorSearchService(
            LocalDocumentStore(settings.data_directory),
            provider,
        ).search(
            arguments.query,
            limit=arguments.limit,
            document_id=arguments.document_id,
        )
    except (IngestionError, ValueError) as error:
        if isinstance(error, IngestionError):
            print(f"Search failed [{error.code}]: {error.message}", file=sys.stderr)
        else:
            print(f"Search configuration is invalid: {error}", file=sys.stderr)
        return 1

    if not results:
        print("No matching embedded document chunks were found.")
        return 0
    for index, result in enumerate(results, start=1):
        location = _result_location(result)
        preview = " ".join(result.chunk.text.split())[:240]
        print(
            f"{index}. {result.document.original_filename}{location} "
            f"(score {result.score:.3f})\n   {preview}"
        )
    return 0


def _result_location(result: VectorSearchResult) -> str:
    location = result.chunk.location
    if location.page_start is not None:
        if location.page_end == location.page_start:
            return f", page {location.page_start}"
        return f", pages {location.page_start}-{location.page_end}"
    if location.headings:
        return f", section {' / '.join(location.headings)}"
    if location.line_start is not None:
        return f", lines {location.line_start}-{location.line_end}"
    return ""
