from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from maintenance_assistant.ingestion import (
    ChunkLocation,
    DocumentLifecycleError,
    DocumentLifecycleErrorCode,
    DocumentLifecycleStatus,
    DocumentFormat,
    DuplicateDocumentError,
    ExtractedDocument,
    ExtractedSegment,
    IngestionError,
    IngestionErrorCode,
    LocalDocumentStore,
    PreparedChunk,
    PreparedEmbedding,
    PreparedParentChunk,
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
        token_count=len(text.split()),
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


def _parent(
    sequence: int = 0,
    text: str = "Check pressure safely.",
) -> PreparedParentChunk:
    return PreparedParentChunk(
        sequence=sequence,
        text=text,
        character_count=len(text),
        token_count=len(text.split()),
        location=ChunkLocation(headings=("Checks",), line_start=1, line_end=2),
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
    assert chunks[0].token_count == 2
    assert chunks[0].location.headings == ("Checks",)


def test_store_saves_parent_context_and_returns_it_with_search(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Check pressure safely.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")
    child = _chunk(text="Check pressure.")
    child = PreparedChunk(
        sequence=child.sequence,
        text=child.text,
        character_count=child.character_count,
        location=child.location,
        token_count=child.token_count,
        parent_sequence=0,
    )

    stored = store.save(
        _document(source),
        [child],
        [_embedding(0, (1.0, 0.0))],
        parents=[_parent()],
    )

    parents = store.list_parent_chunks(stored.id)
    chunks = store.list_chunks(stored.id)
    assert len(parents) == 1
    assert parents[0].text == "Check pressure safely."
    assert store.get_parent_chunk(parents[0].id) == parents[0]
    assert chunks[0].parent_id == parents[0].id
    result = store.search_vectors((1.0, 0.0), model="test-embedding", limit=1)[0]
    assert result.chunk.id == chunks[0].id
    assert result.parent == parents[0]
    text_result = store.search_text("pressure", limit=1)[0]
    assert text_result.chunk.id == chunks[0].id
    assert text_result.parent == parents[0]
    assert text_result.score > 0


@pytest.mark.parametrize(
    ("parents", "children"),
    [
        (
            [_parent()],
            [PreparedChunk(0, "Child", 5, ChunkLocation(), 1, None)],
        ),
        (
            [],
            [PreparedChunk(0, "Child", 5, ChunkLocation(), 1, 3)],
        ),
        (
            [_parent(), _parent()],
            [PreparedChunk(0, "Child", 5, ChunkLocation(), 1, 0)],
        ),
        (
            [_parent(0), _parent(1)],
            [PreparedChunk(0, "Child", 5, ChunkLocation(), 1, 0)],
        ),
        (
            [_parent()],
            [PreparedChunk(0, "Child", 4, ChunkLocation(), 1, 0)],
        ),
    ],
)
def test_store_rejects_invalid_parent_hierarchy(
    tmp_path: Path,
    parents: list[PreparedParentChunk],
    children: list[PreparedChunk],
) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Child", encoding="utf-8")

    with pytest.raises(IngestionError, match="parent|Parent|metadata"):
        LocalDocumentStore(tmp_path / "data").save(
            _document(source),
            children,
            parents=parents,
        )


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


def test_store_lists_documents_with_pagination(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path / "data")
    first_source = tmp_path / "first.txt"
    first_source.write_text("First procedure.", encoding="utf-8")
    first = store.save(_document(first_source), [_chunk(text="First procedure.")])
    second_source = tmp_path / "second.txt"
    second_source.write_text("Second procedure.", encoding="utf-8")
    second = store.save(_document(second_source), [_chunk(text="Second procedure.")])

    assert store.list_documents(limit=1) == (second,)
    assert store.list_documents(limit=1, offset=1) == (first,)


@pytest.mark.parametrize(
    ("limit", "offset", "message"),
    [(0, 0, "limit"), (1, -1, "offset")],
)
def test_store_rejects_invalid_document_pagination(
    tmp_path: Path,
    limit: int,
    offset: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LocalDocumentStore(tmp_path / "data").list_documents(
            limit=limit,
            offset=offset,
        )


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
        connection.execute(
            """
            INSERT INTO documents (
                id, content_hash, original_filename, stored_path,
                document_format, size_bytes, title, page_count, chunk_count,
                extractor_name, extractor_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-document",
                "existing-hash",
                "manual.txt",
                "documents/existing-document/original.txt",
                "text",
                20,
                "Existing manual",
                None,
                1,
                "built-in",
                "1",
                "2026-07-13T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO chunks (
                id, document_id, sequence, text, character_count,
                page_start, page_end, headings, line_start, line_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-chunk",
                "existing-document",
                0,
                "Existing procedure",
                18,
                None,
                None,
                "[]",
                1,
                1,
            ),
        )
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
        conversation_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()
        feedback_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'conversation_message_feedback'"
        ).fetchone()
        jobs_table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'ingestion_jobs'"
        ).fetchone()
    finally:
        connection.close()
    assert version == 12
    assert embedding_table == ("embeddings",)
    assert conversation_table == ("conversations",)
    assert feedback_table == ("conversation_message_feedback",)
    assert jobs_table == ("ingestion_jobs",)
    store = LocalDocumentStore(data_directory)
    migrated = store.get_document("existing-document")
    assert migrated.title == "Existing manual"
    assert migrated.lifecycle_status is DocumentLifecycleStatus.CURRENT
    assert migrated.revision == 1
    assert migrated.lifecycle_updated_at == migrated.created_at
    migrated_chunk = store.list_chunks("existing-document")[0]
    assert migrated_chunk.text == "Existing procedure"
    assert migrated_chunk.token_count is None
    assert migrated_chunk.parent_id is None
    assert store.list_parent_chunks("existing-document") == ()
    assert store.search_text("existing procedure")[0].chunk.id == "existing-chunk"


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
    assert all(result.parent is None for result in results)


def test_store_safely_ranks_exact_fault_codes_with_full_text_search(
    tmp_path: Path,
) -> None:
    store = LocalDocumentStore(tmp_path / "data")
    fault_source = tmp_path / "compressor.txt"
    fault_source.write_text("Fault T-07 indicates high temperature.", encoding="utf-8")
    fault = store.save(
        _document(fault_source),
        [_chunk(text="Fault T-07 indicates high temperature.")],
    )
    other_source = tmp_path / "pump.txt"
    other_source.write_text("Inspect the pump seal.", encoding="utf-8")
    store.save(_document(other_source), [_chunk(text="Inspect the pump seal.")])

    results = store.search_text("What does T-07 mean?", limit=5)

    assert [result.document.id for result in results] == [fault.id]
    assert store.search_text("???") == ()
    assert store.search_text("T-07", document_id="missing") == ()
    with pytest.raises(ValueError, match="limit"):
        store.search_text("fault", limit=0)


def test_store_replaces_current_manual_and_retains_revision_history(
    tmp_path: Path,
) -> None:
    store = LocalDocumentStore(tmp_path / "data")
    first_source = tmp_path / "pump-v1.txt"
    first_source.write_text("Old pump procedure.", encoding="utf-8")
    first = store.save(
        _document(first_source),
        [_chunk(text="Old pump procedure.")],
        [_embedding(0, (1.0, 0.0))],
    )
    second_source = tmp_path / "pump-v2.txt"
    second_source.write_text("Updated pump procedure.", encoding="utf-8")

    second = store.save(
        _document(second_source),
        [_chunk(text="Updated pump procedure.")],
        [_embedding(0, (0.0, 1.0))],
        supersedes_document_id=first.id,
    )

    previous = store.get_document(first.id)
    assert previous.lifecycle_status is DocumentLifecycleStatus.SUPERSEDED
    assert second.lifecycle_status is DocumentLifecycleStatus.CURRENT
    assert second.revision == 2
    assert second.supersedes_document_id == first.id
    assert store.list_revision_history(second.id) == (previous, second)
    assert store.list_documents(
        lifecycle_status=DocumentLifecycleStatus.CURRENT
    ) == (second,)
    results = store.search_vectors((1.0, 0.0), model="test-embedding")
    assert [result.document.id for result in results] == [second.id]


def test_store_archives_manual_and_excludes_its_vectors(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Pump procedure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")
    stored = store.save(
        _document(source),
        [_chunk(text="Pump procedure.")],
        [_embedding(0, (1.0, 0.0))],
    )

    archived = store.archive_document(stored.id)

    assert archived.lifecycle_status is DocumentLifecycleStatus.ARCHIVED
    assert store.search_vectors((1.0, 0.0), model="test-embedding") == ()
    assert store.search_text("pump procedure") == ()


def test_store_permanently_deletes_file_metadata_chunks_and_vectors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("Pump procedure.", encoding="utf-8")
    store = LocalDocumentStore(tmp_path / "data")
    stored = store.save(
        _document(source),
        [_chunk(text="Pump procedure.")],
        [_embedding(0, (1.0, 0.0))],
    )
    stored_directory = stored.stored_path.parent

    store.delete_document(stored.id)

    assert store.get_document(stored.id) is None
    assert store.list_chunks(stored.id) == ()
    assert store.list_embeddings(stored.id) == ()
    assert store.search_text("pump procedure") == ()
    assert not stored_directory.exists()


def test_store_rejects_replacing_a_non_current_manual(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path / "data")
    first_source = tmp_path / "first.txt"
    first_source.write_text("First procedure.", encoding="utf-8")
    first = store.save(_document(first_source), [_chunk(text="First procedure.")])
    store.archive_document(first.id)
    second_source = tmp_path / "second.txt"
    second_source.write_text("Second procedure.", encoding="utf-8")

    with pytest.raises(DocumentLifecycleError) as captured:
        store.save(
            _document(second_source),
            [_chunk(text="Second procedure.")],
            supersedes_document_id=first.id,
        )

    assert captured.value.code is DocumentLifecycleErrorCode.REVISION_CONFLICT
    assert store.find_by_hash(sha256(second_source.read_bytes()).hexdigest()) is None


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


def test_store_persists_bounded_embedding_cache_and_records_hits(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path / "data")
    store.put_cached_embeddings(
        {"first": (1.0, 0.0), "second": (0.0, 1.0)},
        model="test-model",
        dimensions=2,
        max_entries=2,
    )

    cached = store.get_cached_embeddings(
        ["first", "missing"], model="test-model", dimensions=2
    )
    store.put_cached_embeddings(
        {"third": (0.5, 0.5)},
        model="test-model",
        dimensions=2,
        max_entries=2,
    )

    assert cached == {"first": pytest.approx((1.0, 0.0))}
    remaining = store.get_cached_embeddings(
        ["first", "second", "third"], model="test-model", dimensions=2
    )
    assert set(remaining) == {"first", "third"}
    assert store.embedding_cache_stats() == {"entries": 2, "hits": 3}


def test_store_enables_wal_normal_sync_and_configured_busy_timeout(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path / "data", busy_timeout_ms=12_000)

    assert store.sqlite_runtime() == {
        "journal_mode": "wal",
        "synchronous": 1,
        "busy_timeout_ms": 12_000,
    }


def test_store_initialises_once_when_called_concurrently(tmp_path: Path) -> None:
    store = LocalDocumentStore(tmp_path / "data")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: store.initialise(), range(24)))

    with closing(sqlite3.connect(store.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
    assert store._initialised is True


@pytest.mark.parametrize("timeout", [0, 60_001])
def test_store_rejects_invalid_busy_timeout(tmp_path: Path, timeout: int) -> None:
    with pytest.raises(ValueError):
        LocalDocumentStore(tmp_path / "data", timeout)
