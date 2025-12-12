from hashlib import sha256
from pathlib import Path

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import EmbeddingBatch
from maintenance_assistant.ingestion import (
    DocumentMetadata,
    IngestionError,
    IngestionErrorCode,
    IngestionService,
    IngestionStatus,
    LocalDocumentStore,
)
from tests.ingestion.pdf_factory import write_diagram_pdf, write_scanned_pdf, write_text_pdf
from tests.fakes import (
    FixedOCRProvider,
    FixedVisualAnalysisProvider,
    KeywordEmbeddingProvider,
)


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


def test_service_embeds_metadata_and_refreshes_changed_duplicate_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Inspect the seal before starting.", encoding="utf-8")
    provider = KeywordEmbeddingProvider()
    service = IngestionService(_settings(tmp_path), embedding_provider=provider)
    first_metadata = DocumentMetadata(
        brand="  Acme  Industries ",
        machine="PX-4",
        site="North plant",
        document_type="Service manual",
    )

    first = service.ingest(path, first_metadata)

    assert first.document.metadata.brand == "Acme Industries"
    assert "Brand: Acme Industries" in provider.calls[0][0]
    assert "Machine: PX-4" in provider.calls[0][0]
    provider.calls.clear()

    updated = service.ingest(
        path,
        DocumentMetadata(
            brand="Beta Engineering",
            machine="PX-4",
            site="North plant",
            document_type="Service manual",
        ),
    )

    assert updated.status is IngestionStatus.ALREADY_EXISTS
    assert updated.document.id == first.document.id
    assert updated.document.metadata.brand == "Beta Engineering"
    assert provider.calls and "Brand: Beta Engineering" in provider.calls[0][0]


def test_revision_inherits_metadata_and_reindex_uses_it(tmp_path: Path) -> None:
    first_path = tmp_path / "pump-v1.txt"
    first_path.write_text("Inspect the pump seal.", encoding="utf-8")
    replacement_path = tmp_path / "pump-v2.txt"
    replacement_path.write_text("Inspect the pump seal and coupling.", encoding="utf-8")
    provider = KeywordEmbeddingProvider()
    service = IngestionService(_settings(tmp_path), embedding_provider=provider)
    metadata = DocumentMetadata(brand="Acme", machine="PX-4")
    first = service.ingest(first_path, metadata)

    replacement = service.ingest_revision(replacement_path, first.document.id)
    provider.calls.clear()
    service.reindex(replacement.document.id)

    assert replacement.document.metadata == metadata
    assert provider.calls and "Brand: Acme" in provider.calls[0][0]


def test_document_metadata_rejects_unsafe_or_oversized_values() -> None:
    with pytest.raises(ValueError, match="control"):
        DocumentMetadata(brand="Acme\nInjected")
    with pytest.raises(ValueError, match="100"):
        DocumentMetadata(machine="x" * 101)


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


def test_service_ingests_scanned_pdf_through_ocr_pipeline(tmp_path: Path) -> None:
    path = tmp_path / "scanned-manual.pdf"
    write_scanned_pdf(path, "ISOLATE PUMP BEFORE MAINTENANCE")
    settings = _settings(tmp_path)
    provider = FixedOCRProvider("ISOLATE PUMP BEFORE MAINTENANCE")

    result = IngestionService(settings, ocr_provider=provider).ingest(path)

    assert result.status is IngestionStatus.COMPLETED
    assert result.document.extractor_name == "pypdf+test-ocr"
    chunks = LocalDocumentStore(settings.data_directory).list_chunks(result.document.id)
    assert chunks[0].text == "ISOLATE PUMP BEFORE MAINTENANCE"
    assert chunks[0].location.page_start == 1


def test_service_can_explicitly_disable_configured_ocr(tmp_path: Path) -> None:
    service = IngestionService(_settings(tmp_path), ocr_provider=None)

    assert service.ocr_provider is None


def test_service_embeds_visual_descriptions_with_source_traceability(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pump-diagram.pdf"
    write_diagram_pdf(path)
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=20,
        chunk_overlap_tokens=0,
        parent_chunk_size_tokens=60,
    )
    embeddings = KeywordEmbeddingProvider()
    vision = FixedVisualAnalysisProvider()

    result = IngestionService(
        settings,
        embedding_provider=embeddings,
        visual_analysis_provider=vision,
    ).ingest(path)

    store = LocalDocumentStore(settings.data_directory)
    chunks = store.list_chunks(result.document.id)
    visual_chunks = [chunk for chunk in chunks if "Visual analysis" in chunk.text]
    assert visual_chunks
    assert visual_chunks[0].location.page_start == 1
    assert visual_chunks[0].location.headings == (
        "Visual analysis: flow diagram",
    )
    assert result.embedded_chunk_count == len(chunks)
    assert any("Visual analysis" in text for batch in embeddings.calls for text in batch)
    assert result.document.extractor_name == "pypdf+test-vision"


def test_service_can_explicitly_disable_configured_visual_analysis(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_directory=tmp_path / "data",
        visual_analysis_provider="openai",
        openai_api_key="test-key",
    )
    service = IngestionService(settings, visual_analysis_provider=None)

    assert service.visual_analysis_provider is None


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


def test_service_stores_section_parents_for_retrieval_children(tmp_path: Path) -> None:
    path = tmp_path / "pump.md"
    path.write_text(
        "# Isolation\n\nStop and lock the pump.\n\n# Inspection\n\nCheck the seal.",
        encoding="utf-8",
    )
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=5,
        chunk_overlap_tokens=0,
        parent_chunk_size_tokens=30,
    )

    result = IngestionService(settings).ingest(path)

    store = LocalDocumentStore(settings.data_directory)
    parents = store.list_parent_chunks(result.document.id)
    children = store.list_chunks(result.document.id)
    assert [parent.location.headings for parent in parents] == [
        ("Isolation",),
        ("Inspection",),
    ]
    assert all(child.parent_id is not None for child in children)
    assert {child.parent_id for child in children} == {parent.id for parent in parents}


def test_reindex_rebuilds_existing_chunks_with_the_active_hierarchy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pump.txt"
    path.write_text(
        "Pump isolation checks. Valve inspection checks. Motor rotation checks.",
        encoding="utf-8",
    )
    initial_settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_tokens=30,
        chunk_overlap_tokens=0,
        parent_chunk_size_tokens=60,
    )
    first = IngestionService(
        initial_settings,
        embedding_provider=KeywordEmbeddingProvider(),
    ).ingest(path)
    assert first.document.chunk_count == 1
    revised_settings = Settings(
        data_directory=initial_settings.data_directory,
        chunk_size_tokens=4,
        chunk_overlap_tokens=0,
        parent_chunk_size_tokens=12,
    )

    result = IngestionService(
        revised_settings,
        embedding_provider=KeywordEmbeddingProvider(),
    ).reindex(first.document.id)

    store = LocalDocumentStore(revised_settings.data_directory)
    chunks = store.list_chunks(first.document.id)
    assert result.document.id == first.document.id
    assert result.document.chunk_count == len(chunks)
    assert result.embedded_chunk_count == len(chunks)
    assert len(chunks) > 1
    assert all(chunk.token_count is not None and chunk.token_count <= 4 for chunk in chunks)
    assert all(chunk.parent_id is not None for chunk in chunks)
    assert store.list_parent_chunks(first.document.id)
