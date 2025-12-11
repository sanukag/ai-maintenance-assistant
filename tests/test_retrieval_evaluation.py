from __future__ import annotations

import json
from pathlib import Path

import pytest

from maintenance_assistant.cli import evaluation_main
from maintenance_assistant.config import Settings
from maintenance_assistant.evaluation import (
    ExpectedSource,
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
    RetrievalEvaluationError,
    RetrievalEvaluator,
    RetrievalRunConfiguration,
    load_retrieval_dataset,
)
from maintenance_assistant.ingestion import IngestionService, LocalDocumentStore
from maintenance_assistant.retrieval import VectorSearchService
from tests.fakes import KeywordEmbeddingProvider


def _write_dataset(path: Path, cases: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Synthetic maintenance retrieval",
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    return path


def _case(
    identifier: str,
    query: str,
    *,
    document: str | None = None,
    text_contains: str | None = None,
) -> dict[str, object]:
    answerable = document is not None and text_contains is not None
    sources: list[dict[str, str]] = []
    if answerable:
        sources.append({"document": document, "text_contains": text_contains})
    return {
        "id": identifier,
        "query": query,
        "answerable": answerable,
        "relevant_sources": sources,
    }


def _search_service(tmp_path: Path) -> tuple[VectorSearchService, Settings]:
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=20,
        chunk_overlap_tokens=0,
    )
    provider = KeywordEmbeddingProvider()
    pump = tmp_path / "pump-manual.txt"
    pump.write_text(
        "Isolate the pump before removing the mechanical seal.",
        encoding="utf-8",
    )
    valve = tmp_path / "valve-manual.txt"
    valve.write_text(
        "Close the valve before inspecting the actuator.",
        encoding="utf-8",
    )
    service = IngestionService(settings, embedding_provider=provider)
    service.ingest(pump)
    service.ingest(valve)
    return (
        VectorSearchService(LocalDocumentStore(settings.data_directory), provider),
        settings,
    )


def test_loads_versioned_dataset_and_normalises_labels(tmp_path: Path) -> None:
    path = _write_dataset(
        tmp_path / "cases.json",
        [
            {
                "id": "  pump-isolation  ",
                "query": "  How should I isolate the pump?  ",
                "answerable": True,
                "relevant_sources": [
                    {
                        "document": " pump-manual.txt ",
                        "text_contains": " isolate the pump ",
                    }
                ],
            }
        ],
    )

    dataset = load_retrieval_dataset(path)

    assert dataset.name == "Synthetic maintenance retrieval"
    assert dataset.version == 1
    assert dataset.cases[0].identifier == "pump-isolation"
    assert dataset.cases[0].query == "How should I isolate the pump?"
    assert dataset.cases[0].relevant_sources == (
        ExpectedSource("pump-manual.txt", "isolate the pump"),
    )


def test_committed_source_labels_exist_in_the_starter_corpus(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    dataset = load_retrieval_dataset(repository / "evals/retrieval-cases.json")
    settings = Settings(data_directory=tmp_path / "data")
    service = IngestionService(settings)
    documents = {
        path.name: service.ingest(path).document
        for path in sorted((repository / "evals/corpus").glob("*.md"))
    }
    store = LocalDocumentStore(settings.data_directory)

    for case in dataset.cases:
        for source in case.relevant_sources:
            chunks = store.list_chunks(documents[source.document].id)
            expected = " ".join(source.text_contains.casefold().split())
            assert any(
                expected in " ".join(chunk.text.casefold().split())
                for chunk in chunks
            ), case.identifier


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "dataset must be a JSON object"),
        ({"version": 2, "name": "x", "cases": [{}]}, "version must be 1"),
        ({"version": 1, "name": " ", "cases": [{}]}, "name"),
        ({"version": 1, "name": "x", "cases": []}, "non-empty list"),
        ({"version": 1, "name": "x", "cases": "bad"}, "non-empty list"),
        ({"version": 1, "name": "x", "cases": ["bad"]}, "Case 1"),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": " ",
                        "query": "q",
                        "answerable": False,
                        "relevant_sources": [],
                    }
                ],
            },
            "id",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": " ",
                        "answerable": False,
                        "relevant_sources": [],
                    }
                ],
            },
            "query",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": "yes",
                        "relevant_sources": [],
                    }
                ],
            },
            "boolean",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": False,
                        "relevant_sources": None,
                    }
                ],
            },
            "must be a list",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": True,
                        "relevant_sources": [],
                    }
                ],
            },
            "must include a relevant source",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": False,
                        "relevant_sources": [
                            {"document": "manual.txt", "text_contains": "passage"}
                        ],
                    }
                ],
            },
            "must not include relevant sources",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": True,
                        "relevant_sources": ["bad"],
                    }
                ],
            },
            "source 1 must be a JSON object",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": True,
                        "relevant_sources": [
                            {"document": " ", "text_contains": "passage"}
                        ],
                    }
                ],
            },
            "document",
        ),
        (
            {
                "version": 1,
                "name": "x",
                "cases": [
                    {
                        "id": "a",
                        "query": "q",
                        "answerable": True,
                        "relevant_sources": [
                            {"document": "manual.txt", "text_contains": " "}
                        ],
                    }
                ],
            },
            "text_contains",
        ),
    ],
)
def test_rejects_invalid_dataset_shapes(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RetrievalEvaluationError, match=message):
        load_retrieval_dataset(path)


def test_rejects_unreadable_malformed_and_duplicate_datasets(tmp_path: Path) -> None:
    with pytest.raises(RetrievalEvaluationError, match="could not be read"):
        load_retrieval_dataset(tmp_path / "missing.json")

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(RetrievalEvaluationError, match="line 1, column 2"):
        load_retrieval_dataset(malformed)

    duplicate = _write_dataset(
        tmp_path / "duplicate.json",
        [_case("same", "pump"), _case("same", "valve")],
    )
    with pytest.raises(RetrievalEvaluationError, match="identifiers must be unique"):
        load_retrieval_dataset(duplicate)


def test_evaluator_measures_hits_recall_rank_and_abstention(tmp_path: Path) -> None:
    searcher, _ = _search_service(tmp_path)
    dataset = RetrievalEvaluationDataset(
        name="Integration dataset",
        cases=(
            RetrievalEvaluationCase(
                identifier="pump",
                query="pump isolation",
                answerable=True,
                relevant_sources=(
                    ExpectedSource(
                        document="PUMP-MANUAL.TXT",
                        text_contains="isolate   the pump",
                    ),
                    ExpectedSource(
                        document="pump-manual.txt",
                        text_contains="passage that is not present",
                    ),
                ),
            ),
            RetrievalEvaluationCase(
                identifier="valve",
                query="valve inspection",
                answerable=True,
                relevant_sources=(
                    ExpectedSource(
                        document="valve-manual.txt",
                        text_contains="close the valve",
                    ),
                ),
            ),
            RetrievalEvaluationCase(
                identifier="unknown",
                query="bearing lubrication interval",
                answerable=False,
                relevant_sources=(),
            ),
        ),
    )

    report = RetrievalEvaluator(searcher).evaluate(
        dataset,
        limit=2,
        minimum_score=0.9,
        configuration=RetrievalRunConfiguration(
            embedding_model="test-embedding",
            embedding_dimensions=3,
            chunk_size_tokens=20,
            chunk_overlap_tokens=0,
            chunk_token_encoding="cl100k_base",
        ),
    )

    assert report.dataset_name == "Integration dataset"
    assert report.limit == 2
    assert report.minimum_score == 0.9
    assert report.configuration == RetrievalRunConfiguration(
        embedding_model="test-embedding",
        embedding_dimensions=3,
        chunk_size_tokens=20,
        chunk_overlap_tokens=0,
        chunk_token_encoding="cl100k_base",
    )
    assert report.summary.total_cases == 3
    assert report.summary.answerable_cases == 2
    assert report.summary.unanswerable_cases == 1
    assert report.summary.hit_rate_at_k == pytest.approx(1.0)
    assert report.summary.mean_reciprocal_rank == pytest.approx(1.0)
    assert report.summary.mean_recall_at_k == pytest.approx(0.75)
    assert report.summary.unanswerable_no_result_rate == pytest.approx(1.0)
    assert report.summary.mean_latency_ms >= 0
    assert report.summary.p95_latency_ms >= 0
    assert report.cases[0].matched_source_count == 1
    assert report.cases[0].first_relevant_rank == 1
    assert report.cases[0].results[0].relevant is True
    assert report.cases[2].results == ()
    assert report.as_dict()["dataset_name"] == "Integration dataset"


def test_evaluator_handles_an_unanswerable_only_dataset(tmp_path: Path) -> None:
    searcher, _ = _search_service(tmp_path)
    dataset = RetrievalEvaluationDataset(
        name="Unanswerable dataset",
        cases=(
            RetrievalEvaluationCase("unknown", "unknown procedure", False, ()),
        ),
    )

    report = RetrievalEvaluator(searcher).evaluate(dataset, limit=1)

    assert report.summary.hit_rate_at_k is None
    assert report.summary.mean_reciprocal_rank is None
    assert report.summary.mean_recall_at_k is None
    assert report.summary.unanswerable_no_result_rate == 0.0
    assert report.cases[0].reciprocal_rank == 0.0


@pytest.mark.parametrize(
    ("limit", "minimum_score", "message"),
    [
        (0, None, "limit"),
        (1, -1.1, "minimum_score"),
        (1, 1.1, "minimum_score"),
    ],
)
def test_evaluator_rejects_invalid_run_configuration(
    tmp_path: Path,
    limit: int,
    minimum_score: float | None,
    message: str,
) -> None:
    searcher, _ = _search_service(tmp_path)
    dataset = RetrievalEvaluationDataset(
        name="Dataset",
        cases=(RetrievalEvaluationCase("one", "pump", False, ()),),
    )

    with pytest.raises(ValueError, match=message):
        RetrievalEvaluator(searcher).evaluate(
            dataset,
            limit=limit,
            minimum_score=minimum_score,
        )


def test_evaluator_rejects_programmatically_empty_dataset(tmp_path: Path) -> None:
    searcher, _ = _search_service(tmp_path)

    with pytest.raises(ValueError, match="at least one"):
        RetrievalEvaluator(searcher).evaluate(
            RetrievalEvaluationDataset(name="Empty", cases=()),
        )


def test_evaluation_cli_writes_report_and_applies_quality_gates(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    searcher, settings = _search_service(tmp_path)
    dataset = _write_dataset(
        tmp_path / "cases.json",
        [
            _case(
                "pump",
                "pump isolation",
                document="pump-manual.txt",
                text_contains="isolate the pump",
            )
        ],
    )
    report_path = tmp_path / "reports" / "retrieval.json"
    monkeypatch.setenv("AMA_DATA_DIRECTORY", str(settings.data_directory))
    monkeypatch.setenv("AMA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "maintenance_assistant.cli.create_embedding_provider",
        lambda configured: searcher.embedding_provider,
    )

    exit_code = evaluation_main(
        [
            str(dataset),
            "--limit",
            "2",
            "--output",
            str(report_path),
            "--fail-hit-rate-below",
            "1",
            "--fail-mrr-below",
            "1",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert "Evaluated 1 cases" in output.out
    assert "Hit rate@2: 100.0%" in output.out
    assert json.loads(report_path.read_text(encoding="utf-8"))["limit"] == 2
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report["configuration"]["embedding_model"] == "test-embedding"


def test_evaluation_cli_reports_configuration_and_quality_gate_failures(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    invalid_rate = evaluation_main(
        [str(tmp_path / "missing.json"), "--fail-hit-rate-below", "1.1"]
    )
    invalid_output = capsys.readouterr()
    assert invalid_rate == 1
    assert "between 0 and 1" in invalid_output.err

    searcher, settings = _search_service(tmp_path)
    dataset = _write_dataset(
        tmp_path / "miss.json",
        [
            _case(
                "miss",
                "pump isolation",
                document="pump-manual.txt",
                text_contains="passage that is not present",
            )
        ],
    )
    monkeypatch.setenv("AMA_DATA_DIRECTORY", str(settings.data_directory))
    monkeypatch.setenv("AMA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "maintenance_assistant.cli.create_embedding_provider",
        lambda configured: searcher.embedding_provider,
    )

    failed_gate = evaluation_main(
        [
            str(dataset),
            "--fail-hit-rate-below",
            "1",
            "--fail-mrr-below",
            "1",
        ]
    )
    failed_output = capsys.readouterr()
    assert failed_gate == 2
    assert "Quality gate failed: hit rate" in failed_output.err
    assert "Quality gate failed: mean reciprocal rank" in failed_output.err


def test_evaluation_cli_requires_an_embedding_provider(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("AMA_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = evaluation_main([str(tmp_path / "cases.json")])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "AMA_EMBEDDING_PROVIDER=openai" in output.err
