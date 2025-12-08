from hashlib import sha256
from pathlib import Path
import sqlite3
from unittest.mock import patch

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
    PreparedEmbedding,
    SourceLocation,
    ValidatedDocument,
)
from maintenance_assistant.ingestion.storage import _SCHEMA_VERSION_1


def _document(path: Path) -> ExtractedDocument:
    source = ValidatedDocument(
        path=path,
        filename=path.name,
        format=DocumentFormat.TEXT,
        size_bytes=path.stat().st_size,
        content_hash=sha256(path.read_bytes()).hexdigest(),
    )
    return ExtractedDocument(
        source=source,
        title="Pump manual",
        segments=(ExtractedSegment("Check pressure.", SourceLocation(line_start=1)),),
    )


def _chunk(sequence: int = 0, text: str = "Check pressure.") -> PreparedChunk:
    return PreparedChunk(
        sequence=sequence,
        text=text,
        character_count=len(text),
        location=ChunkLocation(headings=("Checks",), line_start=1, line_end=1),
    )


def _embedding(
    sequence: int,
    vector: tuple[float, ...],
    model: str = "test-embedding",
) -> PreparedEmbedding:
    return PreparedEmbedding(
        sequence=sequence,
        model=model,
        dimensions=len(vector),
        vector=vector,
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

    assert store.find_by_hash(first.content_hash) == first
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
    assert store.find_by_hash(sha256(source.read_bytes()).hexdigest()) is None
    assert list(store.documents_directory.iterdir()) == []


def test_store_requires_at_least_one_chunk(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")

    with pytest.raises(IngestionError) as captured:
        LocalDocumentStore(tmp_path / "data").save(_document(source), [])

    assert captured.value.code is IngestionErrorCode.STORAGE_FAILED


def test_store_rejects_source_that_changes_after_validation(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")

    with (
        patch(
            "maintenance_assistant.ingestion.storage._file_hash",
            return_value="changed-hash",
        ),
        pytest.raises(IngestionError) as captured,
    ):
        store.save(_document(source), [_chunk()])

    assert captured.value.code is IngestionErrorCode.INVALID_DOCUMENT
    assert list(store.documents_directory.iterdir()) == []


def test_store_migrates_existing_version_one_database(tmp_path: Path) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    database_path = data_directory / "maintenance-assistant.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(_SCHEMA_VERSION_1)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()

    LocalDocumentStore(data_directory).initialise()

    connection = sqlite3.connect(database_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        embedding_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'embeddings'"
        ).fetchone()
    finally:
        connection.close()
    assert version == 2
    assert embedding_table == ("embeddings",)


def test_store_saves_vectors_with_new_document(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")

    stored = store.save(
        _document(source),
        [_chunk()],
        [_embedding(0, (0.25, 0.75))],
    )

    embeddings = store.list_embeddings(stored.id)
    assert len(embeddings) == 1
    assert embeddings[0].model == "test-embedding"
    assert embeddings[0].dimensions == 2
    assert embeddings[0].vector == pytest.approx((0.25, 0.75))
    assert store.missing_embedding_chunks(
        stored.id, model="test-embedding", dimensions=2
    ) == ()


def test_store_backfills_missing_chunk_vectors(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Pump and valve procedures.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")
    stored = store.save(
        _document(source),
        [_chunk(0, "Pump procedure"), _chunk(1, "Valve procedure")],
    )

    missing = store.missing_embedding_chunks(
        stored.id, model="test-embedding", dimensions=2
    )
    assert [chunk.sequence for chunk in missing] == [0, 1]

    store.save_embeddings(
        stored.id,
        [_embedding(0, (1.0, 0.0)), _embedding(1, (0.0, 1.0))],
    )

    assert store.missing_embedding_chunks(
        stored.id, model="test-embedding", dimensions=2
    ) == ()


def test_store_ranks_vectors_by_cosine_similarity(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Pump valve motor.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")
    stored = store.save(
        _document(source),
        [
            _chunk(0, "Pump procedure"),
            _chunk(1, "Mixed procedure"),
            _chunk(2, "Valve procedure"),
        ],
        [
            _embedding(0, (1.0, 0.0)),
            _embedding(1, (0.8, 0.2)),
            _embedding(2, (0.0, 1.0)),
        ],
    )

    results = store.search_vectors(
        (1.0, 0.0), model="test-embedding", limit=2
    )

    assert [result.chunk.text for result in results] == [
        "Pump procedure",
        "Mixed procedure",
    ]
    assert results[0].score == pytest.approx(1.0)
    assert results[0].document.id == stored.id


@pytest.mark.parametrize("vector", [(), (0.0, 0.0), (float("nan"), 1.0)])
def test_store_rejects_invalid_vectors(
    tmp_path: Path,
    vector: tuple[float, ...],
) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure.", encoding="utf-8")

    with pytest.raises(IngestionError) as captured:
        LocalDocumentStore(tmp_path / "data").save(
            _document(source),
            [_chunk()],
            [_embedding(0, vector)],
        )

    assert captured.value.code is IngestionErrorCode.STORAGE_FAILED
