from pathlib import Path

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import IngestionService, LocalDocumentStore
from maintenance_assistant.retrieval import VectorSearchService
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
    assert provider.calls[-1] == ("How do I repair the pump?",)


def test_vector_search_rejects_empty_query(tmp_path: Path) -> None:
    service = VectorSearchService(
        LocalDocumentStore(tmp_path / "data"),
        KeywordEmbeddingProvider(),
    )

    with pytest.raises(ValueError, match="query"):
        service.search("  ")
