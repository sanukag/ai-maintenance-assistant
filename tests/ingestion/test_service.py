from pathlib import Path

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import (
    IngestionService,
    IngestionStatus,
    LocalDocumentStore,
)
from tests.ingestion.pdf_factory import write_text_pdf


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_directory=tmp_path / "data",
        chunk_size_characters=45,
        chunk_overlap_characters=10,
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
