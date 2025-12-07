from pathlib import Path

import pytest

from maintenance_assistant.ingestion import (
    ChunkLocation,
    DocumentFormat,
    DuplicateDocumentError,
    ExtractedDocument,
    ExtractedSegment,
    IngestionError,
    IngestionErrorCode,
    LocalDocumentStore,
    PreparedChunk,
    SourceLocation,
    ValidatedDocument,
)


def _document(path: Path, content_hash: str = "content-hash") -> ExtractedDocument:
    source = ValidatedDocument(
        path=path,
        filename=path.name,
        format=DocumentFormat.TEXT,
        size_bytes=path.stat().st_size,
        content_hash=content_hash,
    )
    return ExtractedDocument(
        source=source,
        title="Pump manual",
        segments=(ExtractedSegment("Check pressure.", SourceLocation(line_start=1)),),
    )


def _chunk(sequence: int = 0) -> PreparedChunk:
    return PreparedChunk(
        sequence=sequence,
        text="Check pressure.",
        character_count=15,
        location=ChunkLocation(headings=("Checks",), line_start=1, line_end=1),
    )


def test_store_saves_source_document_and_chunks(tmp_path: Path) -> None:
    source = tmp_path / "source" / "manual.txt"
    source.parent.mkdir()
    source.write_text("Check pressure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")

    stored = store.save(_document(source), [_chunk()])

    assert stored.original_filename == "manual.txt"
    assert stored.stored_path.read_text(encoding="utf-8") == "Check pressure."
    assert stored.stored_path.name == "original.txt"
    assert stored.chunk_count == 1
    assert store.get_document(stored.id) == stored
    chunks = store.list_chunks(stored.id)
    assert chunks[0].text == "Check pressure."
    assert chunks[0].location.headings == ("Checks",)


def test_store_finds_and_rejects_duplicate_content(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")
    first = store.save(_document(source), [_chunk()])

    assert store.find_by_hash("content-hash") == first
    with pytest.raises(DuplicateDocumentError) as captured:
        store.save(_document(source), [_chunk()])

    assert captured.value.document_id == first.id
    stored_directories = [
        path for path in store.documents_directory.iterdir() if path.is_dir()
    ]
    assert stored_directories == [first.stored_path.parent]


def test_store_rolls_back_files_when_chunk_storage_fails(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")

    with pytest.raises(IngestionError) as captured:
        store.save(_document(source), [_chunk(), _chunk()])

    assert captured.value.code is IngestionErrorCode.STORAGE_FAILED
    assert store.find_by_hash("content-hash") is None
    assert list(store.documents_directory.iterdir()) == []


def test_store_requires_at_least_one_chunk(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")

    with pytest.raises(IngestionError) as captured:
        LocalDocumentStore(tmp_path / "data").save(_document(source), [])

    assert captured.value.code is IngestionErrorCode.STORAGE_FAILED
