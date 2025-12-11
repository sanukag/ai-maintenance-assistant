from pathlib import Path

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import IngestionService, LocalDocumentStore
from maintenance_assistant.retrieval import HybridSearchService, VectorSearchService
from tests.fakes import KeywordEmbeddingProvider


def test_vector_search_embeds_query_and_ranks_local_chunks(tmp_path: Path) -> None:
    path = tmp_path / "procedures.txt"
    path.write_text(
        "Pump seal replacement.\n\nValve isolation procedure.",
        encoding="utf-8",
    )
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=7,
        chunk_overlap_tokens=0,
    )
    provider = KeywordEmbeddingProvider()
    ingested = IngestionService(
        settings,
        embedding_provider=provider,
    ).ingest(path)

    results = VectorSearchService(
        LocalDocumentStore(settings.data_directory),
        provider,
    ).search("How do I repair the pump?", limit=1)

    assert len(results) == 1
    assert results[0].document.id == ingested.document.id
    assert results[0].chunk.text == "Pump seal replacement."
    assert results[0].score == pytest.approx(1.0)
    assert results[0].semantic_score == pytest.approx(1.0)
    assert results[0].retrieval_methods == ("semantic",)
    assert provider.calls[-1] == ("How do I repair the pump?",)


def test_vector_search_rejects_empty_query(tmp_path: Path) -> None:
    service = VectorSearchService(
        LocalDocumentStore(tmp_path / "data"),
        KeywordEmbeddingProvider(),
    )

    with pytest.raises(ValueError, match="query"):
        service.search("  ")


def test_hybrid_search_fuses_exact_text_with_semantic_candidates(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=30,
        chunk_overlap_tokens=0,
    )
    provider = KeywordEmbeddingProvider()
    service = IngestionService(settings, embedding_provider=provider)
    fault = tmp_path / "compressor.txt"
    fault.write_text("Valve fault T-07 indicates high temperature.", encoding="utf-8")
    fault_document = service.ingest(fault).document
    general = tmp_path / "general.txt"
    general.write_text("General inspection guidance.", encoding="utf-8")
    service.ingest(general)
    search = HybridSearchService(
        LocalDocumentStore(settings.data_directory),
        provider,
        candidate_limit=10,
    )

    results = search.search("What does T-07 mean?", limit=2)

    assert results[0].document.id == fault_document.id
    assert results[0].retrieval_methods == ("semantic", "text")
    assert results[0].semantic_score is not None
    assert results[0].lexical_score is not None
    assert results[0].score > results[1].score
    filtered = search.search(
        "T-07",
        limit=2,
        document_id=fault_document.id,
    )
    assert [result.document.id for result in filtered] == [fault_document.id]


def test_hybrid_search_can_run_with_text_ranking_only(tmp_path: Path) -> None:
    settings = Settings(data_directory=tmp_path / "data")
    provider = KeywordEmbeddingProvider()
    path = tmp_path / "compressor.txt"
    path.write_text("Reset code T-07 after inspection.", encoding="utf-8")
    IngestionService(settings, embedding_provider=provider).ingest(path)
    provider.calls.clear()
    search = HybridSearchService(
        LocalDocumentStore(settings.data_directory),
        provider,
        semantic_weight=0,
        text_weight=1,
    )

    result = search.search("T-07", limit=1)[0]

    assert result.retrieval_methods == ("text",)
    assert result.semantic_score is None
    assert result.lexical_score is not None
    assert result.score == pytest.approx(1.0)
    assert provider.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"candidate_limit": 0},
        {"rrf_k": 0},
        {"semantic_weight": -1},
        {"text_weight": -1},
        {"semantic_weight": float("nan")},
        {"text_weight": float("inf")},
        {"semantic_weight": 0, "text_weight": 0},
    ],
)
def test_hybrid_search_rejects_invalid_configuration(
    tmp_path: Path,
    arguments: dict[str, float],
) -> None:
    with pytest.raises(ValueError):
        HybridSearchService(
            LocalDocumentStore(tmp_path / "data"),
            KeywordEmbeddingProvider(),
            **arguments,
        )


def test_hybrid_search_rejects_invalid_queries(tmp_path: Path) -> None:
    search = HybridSearchService(
        LocalDocumentStore(tmp_path / "data"),
        KeywordEmbeddingProvider(),
    )

    with pytest.raises(ValueError, match="query"):
        search.search(" ")
    with pytest.raises(ValueError, match="limit"):
        search.search("pump", limit=0)
