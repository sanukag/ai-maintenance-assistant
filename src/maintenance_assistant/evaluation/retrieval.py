"""Deterministic evaluation of retrieval against labelled source passages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Protocol

from maintenance_assistant.ingestion.models import VectorSearchResult


class RetrievalEvaluationError(ValueError):
    """A retrieval dataset cannot be loaded or evaluated safely."""


@dataclass(frozen=True, slots=True)
class ExpectedSource:
    """A stable source passage that should satisfy an evaluation query."""

    document: str
    text_contains: str


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationCase:
    """One maintenance question and its expected supporting passages."""

    identifier: str
    query: str
    answerable: bool
    relevant_sources: tuple[ExpectedSource, ...]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationDataset:
    """A versioned collection of retrieval evaluation cases."""

    name: str
    cases: tuple[RetrievalEvaluationCase, ...]
    version: int = 1


@dataclass(frozen=True, slots=True)
class RetrievedChunkResult:
    """A compact, serialisable view of one retrieved chunk."""

    rank: int
    document: str
    chunk_sequence: int
    score: float
    relevant: bool


@dataclass(frozen=True, slots=True)
class RetrievalCaseResult:
    """Measured retrieval behaviour for one evaluation case."""

    identifier: str
    query: str
    answerable: bool
    retrieved_count: int
    matched_source_count: int
    first_relevant_rank: int | None
    reciprocal_rank: float
    recall_at_k: float | None
    latency_ms: float
    results: tuple[RetrievedChunkResult, ...]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationSummary:
    """Aggregate metrics for one evaluation run."""

    total_cases: int
    answerable_cases: int
    unanswerable_cases: int
    hit_rate_at_k: float | None
    mean_reciprocal_rank: float | None
    mean_recall_at_k: float | None
    unanswerable_no_result_rate: float | None
    mean_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True, slots=True)
class RetrievalRunConfiguration:
    """Settings needed to reproduce a local retrieval evaluation run."""

    embedding_model: str
    embedding_dimensions: int
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    parent_chunk_size_tokens: int
    chunk_token_encoding: str
    retrieval_candidate_limit: int
    retrieval_rrf_k: int
    retrieval_semantic_weight: float
    retrieval_text_weight: float


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    """Complete retrieval evaluation results and run configuration."""

    dataset_name: str
    dataset_version: int
    limit: int
    minimum_score: float | None
    configuration: RetrievalRunConfiguration | None
    summary: RetrievalEvaluationSummary
    cases: tuple[RetrievalCaseResult, ...]

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible report data without adding volatile metadata."""

        return asdict(self)


class RetrievalSearcher(Protocol):
    """The search surface required by the evaluator."""

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        document_id: str | None = None,
    ) -> Sequence[VectorSearchResult]: ...


class RetrievalEvaluator:
    """Run labelled questions through a retriever and calculate ranking metrics."""

    def __init__(self, searcher: RetrievalSearcher) -> None:
        self.searcher = searcher

    def evaluate(
        self,
        dataset: RetrievalEvaluationDataset,
        *,
        limit: int = 5,
        minimum_score: float | None = None,
        configuration: RetrievalRunConfiguration | None = None,
    ) -> RetrievalEvaluationReport:
        """Evaluate a dataset at the requested result limit and score threshold."""

        if limit < 1:
            raise ValueError("limit must be greater than zero")
        if minimum_score is not None and not -1.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between -1 and 1")
        if not dataset.cases:
            raise ValueError("dataset must contain at least one case")

        case_results = tuple(
            self._evaluate_case(case, limit=limit, minimum_score=minimum_score)
            for case in dataset.cases
        )
        return RetrievalEvaluationReport(
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            limit=limit,
            minimum_score=minimum_score,
            configuration=configuration,
            summary=_summarise(case_results),
            cases=case_results,
        )

    def _evaluate_case(
        self,
        case: RetrievalEvaluationCase,
        *,
        limit: int,
        minimum_score: float | None,
    ) -> RetrievalCaseResult:
        started = perf_counter()
        candidates = tuple(self.searcher.search(case.query, limit=limit))
        if minimum_score is not None:
            candidates = tuple(
                result for result in candidates if result.score >= minimum_score
            )
        latency_ms = (perf_counter() - started) * 1_000

        matched_sources = {
            source_index
            for source_index, source in enumerate(case.relevant_sources)
            if any(_matches_source(result, source) for result in candidates)
        }
        ranked = tuple(
            RetrievedChunkResult(
                rank=rank,
                document=result.document.original_filename,
                chunk_sequence=result.chunk.sequence,
                score=result.score,
                relevant=any(
                    _matches_source(result, source)
                    for source in case.relevant_sources
                ),
            )
            for rank, result in enumerate(candidates, start=1)
        )
        first_relevant_rank = next(
            (result.rank for result in ranked if result.relevant),
            None,
        )
        recall = (
            len(matched_sources) / len(case.relevant_sources)
            if case.relevant_sources
            else None
        )
        return RetrievalCaseResult(
            identifier=case.identifier,
            query=case.query,
            answerable=case.answerable,
            retrieved_count=len(ranked),
            matched_source_count=len(matched_sources),
            first_relevant_rank=first_relevant_rank,
            reciprocal_rank=(1 / first_relevant_rank if first_relevant_rank else 0.0),
            recall_at_k=recall,
            latency_ms=latency_ms,
            results=ranked,
        )


def load_retrieval_dataset(path: Path) -> RetrievalEvaluationDataset:
    """Load and validate a version-one retrieval dataset from JSON."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RetrievalEvaluationError(f"Dataset could not be read: {path}") from error
    except json.JSONDecodeError as error:
        raise RetrievalEvaluationError(
            f"Dataset is not valid JSON: line {error.lineno}, column {error.colno}"
        ) from error

    root = _mapping(raw, "dataset")
    version = root.get("version")
    if version != 1:
        raise RetrievalEvaluationError("Dataset version must be 1")
    name = _non_empty_string(root.get("name"), "Dataset name")
    raw_cases = root.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise RetrievalEvaluationError("Dataset cases must be a non-empty list")

    cases = tuple(_load_case(raw_case, index) for index, raw_case in enumerate(raw_cases))
    identifiers = [case.identifier for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise RetrievalEvaluationError("Dataset case identifiers must be unique")
    return RetrievalEvaluationDataset(name=name, cases=cases, version=version)


def _load_case(raw: object, index: int) -> RetrievalEvaluationCase:
    case = _mapping(raw, f"Case {index + 1}")
    identifier = _non_empty_string(case.get("id"), f"Case {index + 1} id")
    query = _non_empty_string(case.get("query"), f"Case {identifier} query")
    answerable = case.get("answerable")
    if not isinstance(answerable, bool):
        raise RetrievalEvaluationError(f"Case {identifier} answerable must be boolean")
    raw_sources = case.get("relevant_sources")
    if not isinstance(raw_sources, list):
        raise RetrievalEvaluationError(
            f"Case {identifier} relevant_sources must be a list"
        )
    sources = tuple(
        _load_source(raw_source, identifier, source_index)
        for source_index, raw_source in enumerate(raw_sources)
    )
    if answerable and not sources:
        raise RetrievalEvaluationError(
            f"Answerable case {identifier} must include a relevant source"
        )
    if not answerable and sources:
        raise RetrievalEvaluationError(
            f"Unanswerable case {identifier} must not include relevant sources"
        )
    return RetrievalEvaluationCase(
        identifier=identifier,
        query=query,
        answerable=answerable,
        relevant_sources=sources,
    )


def _load_source(raw: object, case_id: str, index: int) -> ExpectedSource:
    source = _mapping(raw, f"Case {case_id} source {index + 1}")
    return ExpectedSource(
        document=_non_empty_string(
            source.get("document"),
            f"Case {case_id} source {index + 1} document",
        ),
        text_contains=_non_empty_string(
            source.get("text_contains"),
            f"Case {case_id} source {index + 1} text_contains",
        ),
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RetrievalEvaluationError(f"{label} must be a JSON object")
    return value


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalEvaluationError(f"{label} must be a non-empty string")
    return value.strip()


def _matches_source(result: VectorSearchResult, source: ExpectedSource) -> bool:
    return (
        result.document.original_filename.casefold() == source.document.casefold()
        and _normalise_text(source.text_contains)
        in _normalise_text(result.chunk.text)
    )


def _normalise_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _summarise(
    case_results: Sequence[RetrievalCaseResult],
) -> RetrievalEvaluationSummary:
    answerable = tuple(result for result in case_results if result.answerable)
    unanswerable = tuple(result for result in case_results if not result.answerable)
    latencies = sorted(result.latency_ms for result in case_results)
    return RetrievalEvaluationSummary(
        total_cases=len(case_results),
        answerable_cases=len(answerable),
        unanswerable_cases=len(unanswerable),
        hit_rate_at_k=_mean(
            tuple(result.first_relevant_rank is not None for result in answerable)
        ),
        mean_reciprocal_rank=_mean(
            tuple(result.reciprocal_rank for result in answerable)
        ),
        mean_recall_at_k=_mean(
            tuple(
                result.recall_at_k
                for result in answerable
                if result.recall_at_k is not None
            )
        ),
        unanswerable_no_result_rate=_mean(
            tuple(result.retrieved_count == 0 for result in unanswerable)
        ),
        mean_latency_ms=sum(latencies) / len(latencies),
        p95_latency_ms=latencies[ceil(len(latencies) * 0.95) - 1],
    )


def _mean(values: Sequence[float | bool]) -> float | None:
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)
