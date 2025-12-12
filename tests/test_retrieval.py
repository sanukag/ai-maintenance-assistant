from pathlib import Path

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import (
    DocumentMetadata,
    IngestionService,
    LocalDocumentStore,
)
from maintenance_assistant.retrieval import HybridSearchService, VectorSearchService
from maintenance_assistant.vision import VisualAnalysis, VisualType
from tests.fakes import FixedVisualAnalysisProvider, KeywordEmbeddingProvider
from tests.ingestion.pdf_factory import write_diagram_pdf


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


def test_hybrid_search_embeds_and_applies_case_insensitive_metadata_filters(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=30,
        chunk_overlap_tokens=0,
    )
    provider = KeywordEmbeddingProvider()
    ingestion = IngestionService(settings, embedding_provider=provider)
    acme = tmp_path / "acme.txt"
    acme.write_text("Pump seal procedure for line alpha.", encoding="utf-8")
    acme_document = ingestion.ingest(
        acme,
        DocumentMetadata(
            brand="Acme",
            machine="P-100",
            site="North plant",
            document_type="Service manual",
        ),
    ).document
    beta = tmp_path / "beta.txt"
    beta.write_text("Pump seal procedure for line beta.", encoding="utf-8")
    ingestion.ingest(
        beta,
        DocumentMetadata(brand="Beta", machine="P-200", site="South plant"),
    )
    search = HybridSearchService(LocalDocumentStore(settings.data_directory), provider)
    provider.calls.clear()

    results = search.search(
        "pump seal",
        limit=10,
        metadata=DocumentMetadata(brand="ACME", machine="P-100"),
    )
    missing = search.search(
        "pump seal",
        limit=10,
        metadata=DocumentMetadata(site="Unknown site"),
    )

    assert {result.document.id for result in results} == {acme_document.id}
    assert "Brand: ACME" in provider.calls[0][0]
    assert "Machine: P-100" in provider.calls[0][0]
    assert missing == ()


def test_hybrid_search_retrieves_page_cited_visual_meaning(tmp_path: Path) -> None:
    path = tmp_path / "pump-diagram.pdf"
    write_diagram_pdf(path)
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=25,
        chunk_overlap_tokens=0,
        parent_chunk_size_tokens=80,
    )
    embeddings = KeywordEmbeddingProvider()
    vision = FixedVisualAnalysisProvider(
        VisualAnalysis(
            visual_type=VisualType.FLOW_DIAGRAM,
            summary="A red bypass valve labelled BV-2 bridges the pump outlet.",
            components=("Bypass valve BV-2",),
            relationships=("BV-2 connects across the pump outlet",),
        )
    )
    document = IngestionService(
        settings,
        embedding_provider=embeddings,
        visual_analysis_provider=vision,
    ).ingest(path).document
    search = HybridSearchService(
        LocalDocumentStore(settings.data_directory),
        embeddings,
    )

    result = search.search("Where is BV-2 shown?", limit=1)[0]

    assert result.document.id == document.id
    assert "BV-2" in result.chunk.text
    assert result.chunk.location.page_start == 1
    assert result.chunk.location.headings == ("Visual analysis: flow diagram",)
    assert "text" in result.retrieval_methods


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
