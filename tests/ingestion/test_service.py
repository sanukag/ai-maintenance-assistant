from hashlib import sha256
from pathlib import Path

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import EmbeddingBatch
from maintenance_assistant.ingestion import (
    IngestionError,
    IngestionErrorCode,
    IngestionService,
    IngestionStatus,
    LocalDocumentStore,
)
from tests.ingestion.pdf_factory import write_text_pdf
from tests.fakes import KeywordEmbeddingProvider


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=10,
        chunk_overlap_tokens=2,
    )


def test_service_ingests_text_document_end_to_end(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    path.write_text(
        "Pump checks\n\nCheck the seal before starting.\n\nRecord the pressure.",
        encoding="utf-8",
    )
    settings = _settings(tmp_path)
    service = IngestionService(settings)

    result = service.ingest(path)

    assert result.status is IngestionStatus.COMPLETED
    assert result.document.original_filename == "pump.txt"
    assert result.document.chunk_count == 2
    assert result.document.stored_path.is_file()
    chunks = LocalDocumentStore(settings.data_directory).list_chunks(result.document.id)
    assert [chunk.sequence for chunk in chunks] == [0, 1]
    assert "Record the pressure." in chunks[1].text


def test_service_returns_existing_document_for_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "pump.md"
    path.write_text("# Pump\n\nCheck pressure.", encoding="utf-8")
    service = IngestionService(_settings(tmp_path))

    first = service.ingest(path)
    second = service.ingest(path)

    assert first.status is IngestionStatus.COMPLETED
    assert second.status is IngestionStatus.ALREADY_EXISTS
    assert second.document.id == first.document.id


def test_service_ingests_real_pdf_with_page_traceability(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    write_text_pdf(path, "Isolate the pump before maintenance.")
    settings = _settings(tmp_path)

    result = IngestionService(settings).ingest(path)

    assert result.status is IngestionStatus.COMPLETED
    assert result.document.page_count == 1
    assert result.document.extractor_name == "pypdf"
    chunks = LocalDocumentStore(settings.data_directory).list_chunks(result.document.id)
    assert chunks[0].text == "Isolate the pump before maintenance."
    assert chunks[0].location.page_start == 1
    assert chunks[0].location.page_end == 1


def test_service_embeds_chunks_during_ingestion(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Pump checks.\n\nValve checks.", encoding="utf-8")
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=4,
        chunk_overlap_tokens=0,
    )
    provider = KeywordEmbeddingProvider()

    result = IngestionService(
        settings,
        embedding_provider=provider,
    ).ingest(path)

    assert result.status is IngestionStatus.COMPLETED
    assert result.embedded_chunk_count == 2
    assert result.embedding_model == "test-embedding"
    assert result.embedding_input_tokens == 4
    stored = LocalDocumentStore(settings.data_directory)
    embeddings = stored.list_embeddings(
        result.document.id,
        model="test-embedding",
        dimensions=3,
    )
    assert [embedding.vector for embedding in embeddings] == [
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    ]


def test_service_backfills_embeddings_for_existing_document(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Pump checks.", encoding="utf-8")
    settings = _settings(tmp_path)
    first = IngestionService(settings).ingest(path)
    provider = KeywordEmbeddingProvider()

    second = IngestionService(
        settings,
        embedding_provider=provider,
    ).ingest(path)

    assert second.status is IngestionStatus.ALREADY_EXISTS
    assert second.document.id == first.document.id
    assert second.embedded_chunk_count == 1
    assert second.embedding_input_tokens == 2
    assert len(provider.calls) == 1


def test_service_reuses_existing_embeddings_without_api_call(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Pump checks.", encoding="utf-8")
    settings = _settings(tmp_path)
    first_provider = KeywordEmbeddingProvider()
    first = IngestionService(
        settings,
        embedding_provider=first_provider,
    ).ingest(path)
    second_provider = KeywordEmbeddingProvider()

    second = IngestionService(
        settings,
        embedding_provider=second_provider,
    ).ingest(path)

    assert second.document.id == first.document.id
    assert second.embedded_chunk_count == 1
    assert second.embedding_input_tokens == 0
    assert second_provider.calls == []


def test_service_does_not_store_document_for_invalid_embedding_batch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Pump checks.", encoding="utf-8")
    settings = _settings(tmp_path)
    provider = KeywordEmbeddingProvider()
    provider.embed = lambda texts: EmbeddingBatch(
        model=provider.model,
        dimensions=provider.dimensions,
        vectors=(),
        input_tokens=0,
    )

    with pytest.raises(IngestionError) as captured:
        IngestionService(settings, embedding_provider=provider).ingest(path)

    assert captured.value.code is IngestionErrorCode.EMBEDDING_FAILED
    content_hash = sha256(path.read_bytes()).hexdigest()
    assert LocalDocumentStore(settings.data_directory).find_by_hash(content_hash) is None
