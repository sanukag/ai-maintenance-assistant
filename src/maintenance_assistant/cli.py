"""Command-line interface for local document ingestion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import create_embedding_provider
from maintenance_assistant.evaluation import (
    RetrievalEvaluationError,
    RetrievalEvaluationReport,
    RetrievalEvaluator,
    RetrievalRunConfiguration,
    load_retrieval_dataset,
)
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


def evaluation_main(argv: Sequence[str] | None = None) -> int:
    """Measure local retrieval against a labelled JSON dataset."""

    parser = argparse.ArgumentParser(
        prog="ama-evaluate-retrieval",
        description="Evaluate retrieval quality against labelled source passages.",
    )
    parser.add_argument("dataset", type=Path, help="path to a retrieval dataset")
    parser.add_argument("--limit", type=int, default=5, help="results per question")
    parser.add_argument(
        "--minimum-score",
        type=float,
        help="discard results below this cosine similarity score",
    )
    parser.add_argument("--output", type=Path, help="write the complete JSON report")
    parser.add_argument(
        "--fail-hit-rate-below",
        type=float,
        help="return a failing status when hit rate is below this value",
    )
    parser.add_argument(
        "--fail-mrr-below",
        type=float,
        help="return a failing status when mean reciprocal rank is below this value",
    )
    arguments = parser.parse_args(argv)

    try:
        _validate_rate(arguments.fail_hit_rate_below, "--fail-hit-rate-below")
        _validate_rate(arguments.fail_mrr_below, "--fail-mrr-below")
        settings = Settings.from_environment()
        provider = create_embedding_provider(settings)
        if provider is None:
            raise ValueError(
                "retrieval evaluation requires AMA_EMBEDDING_PROVIDER=openai"
            )
        dataset = load_retrieval_dataset(arguments.dataset)
        report = RetrievalEvaluator(
            VectorSearchService(
                LocalDocumentStore(settings.data_directory),
                provider,
            )
        ).evaluate(
            dataset,
            limit=arguments.limit,
            minimum_score=arguments.minimum_score,
            configuration=RetrievalRunConfiguration(
                embedding_model=provider.model,
                embedding_dimensions=provider.dimensions,
                chunk_size_tokens=settings.chunk_size_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
                parent_chunk_size_tokens=settings.parent_chunk_size_tokens,
                chunk_token_encoding=settings.chunk_token_encoding,
            ),
        )
        if arguments.output is not None:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (IngestionError, RetrievalEvaluationError, ValueError, OSError) as error:
        print(f"Retrieval evaluation failed: {error}", file=sys.stderr)
        return 1

    _print_evaluation_summary(report)
    failed_thresholds = _failed_thresholds(
        report,
        hit_rate=arguments.fail_hit_rate_below,
        mean_reciprocal_rank=arguments.fail_mrr_below,
    )
    if failed_thresholds:
        for message in failed_thresholds:
            print(f"Quality gate failed: {message}", file=sys.stderr)
        return 2
    return 0


def _validate_rate(value: float | None, option: str) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{option} must be between 0 and 1")


def _print_evaluation_summary(report: RetrievalEvaluationReport) -> None:
    summary = report.summary
    print(
        f"Evaluated {summary.total_cases} cases from {report.dataset_name} "
        f"at k={report.limit}."
    )
    print(f"Hit rate@{report.limit}: {_rate(summary.hit_rate_at_k)}")
    print(f"Mean reciprocal rank: {_rate(summary.mean_reciprocal_rank)}")
    print(f"Mean recall@{report.limit}: {_rate(summary.mean_recall_at_k)}")
    print(
        "Unanswerable no-result rate: "
        f"{_rate(summary.unanswerable_no_result_rate)}"
    )
    print(f"Mean retrieval latency: {summary.mean_latency_ms:.2f} ms")


def _rate(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.1%}"


def _failed_thresholds(
    report: RetrievalEvaluationReport,
    *,
    hit_rate: float | None,
    mean_reciprocal_rank: float | None,
) -> tuple[str, ...]:
    failures: list[str] = []
    measured_hit_rate = report.summary.hit_rate_at_k
    if hit_rate is not None and (
        measured_hit_rate is None or measured_hit_rate < hit_rate
    ):
        failures.append(
            f"hit rate {_rate(measured_hit_rate)} is below {hit_rate:.1%}"
        )
    measured_mrr = report.summary.mean_reciprocal_rank
    if mean_reciprocal_rank is not None and (
        measured_mrr is None or measured_mrr < mean_reciprocal_rank
    ):
        failures.append(
            "mean reciprocal rank "
            f"{_rate(measured_mrr)} is below {mean_reciprocal_rank:.1%}"
        )
    return tuple(failures)
