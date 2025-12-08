"""Transactional SQLite and file storage for ingested documents."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isfinite, sqrt
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
from typing import Sequence
from uuid import uuid4

from maintenance_assistant.ingestion.errors import (
    DuplicateDocumentError,
    IngestionError,
    IngestionErrorCode,
)
from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    DocumentFormat,
    ExtractedDocument,
    PreparedChunk,
    PreparedEmbedding,
    StoredChunk,
    StoredDocument,
    StoredEmbedding,
    VectorSearchResult,
)

_SCHEMA_VERSION_1 = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL UNIQUE,
    document_format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    title TEXT NOT NULL,
    page_count INTEGER,
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
    extractor_name TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    text TEXT NOT NULL CHECK (length(text) > 0),
    character_count INTEGER NOT NULL CHECK (character_count > 0),
    page_start INTEGER,
    page_end INTEGER,
    headings TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    UNIQUE (document_id, sequence)
);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks(document_id);
"""

_MIGRATION_VERSION_2 = """
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    vector BLOB NOT NULL CHECK (length(vector) = dimensions * 4),
    magnitude REAL NOT NULL CHECK (magnitude > 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (chunk_id, model, dimensions)
);

CREATE INDEX IF NOT EXISTS embeddings_model_dimensions_idx
ON embeddings(model, dimensions);
"""

_CURRENT_SCHEMA_VERSION = 2


class LocalDocumentStore:
    """Persist original files, document metadata and chunks under one data root."""

    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory.expanduser().resolve()
        self.database_path = self.data_directory / "maintenance-assistant.db"
        self.documents_directory = self.data_directory / "documents"

    def initialise(self) -> None:
        """Create local directories and migrate the database to the current schema."""

        self.documents_directory.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    connection.executescript(_SCHEMA_VERSION_1)
                    connection.execute("PRAGMA user_version = 1")
                    version = 1
                if version == 1:
                    connection.executescript(_MIGRATION_VERSION_2)
                    connection.execute("PRAGMA user_version = 2")
                    version = 2
                if version != _CURRENT_SCHEMA_VERSION:
                    raise sqlite3.DatabaseError(
                        f"Unsupported database schema version: {version}"
                    )
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Local document storage could not be initialised",
            ) from error

    def find_by_hash(self, content_hash: str) -> StoredDocument | None:
        """Find an existing document by its content fingerprint."""

        self.initialise()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM documents WHERE content_hash = ?",
                    (content_hash,),
                ).fetchone()
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Local document storage could not be queried",
            ) from error
        return self._document_from_row(row) if row else None

    def get_document(self, document_id: str) -> StoredDocument | None:
        """Return one stored document by identifier."""

        self.initialise()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        return self._document_from_row(row) if row else None

    def list_chunks(self, document_id: str) -> tuple[StoredChunk, ...]:
        """Return stored chunks in document order."""

        self.initialise()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM chunks WHERE document_id = ? ORDER BY sequence",
                (document_id,),
            ).fetchall()
        return tuple(self._chunk_from_row(row) for row in rows)

    def save(
        self,
        document: ExtractedDocument,
        chunks: Sequence[PreparedChunk],
        embeddings: Sequence[PreparedEmbedding] = (),
    ) -> StoredDocument:
        """Atomically store a fully prepared document and its chunks."""

        if not chunks:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "A document must contain at least one chunk",
            )
        _validate_prepared_embeddings(chunks, embeddings, require_complete=True)
        self.initialise()
        document_id = str(uuid4())
        created_at = datetime.now(UTC)
        final_directory = self.documents_directory / document_id
        stored_filename = f"original{document.source.path.suffix.lower()}"
        relative_path = Path("documents") / document_id / stored_filename
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{document_id}-", dir=self.documents_directory)
        )
        copied_path = temporary_directory / stored_filename
        moved_to_final = False
        committed = False

        try:
            shutil.copy2(document.source.path, copied_path)
            if _file_hash(copied_path) != document.source.content_hash:
                raise IngestionError(
                    IngestionErrorCode.INVALID_DOCUMENT,
                    "Document changed while it was being ingested; try again",
                )
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                duplicate = connection.execute(
                    "SELECT id FROM documents WHERE content_hash = ?",
                    (document.source.content_hash,),
                ).fetchone()
                if duplicate:
                    raise DuplicateDocumentError(duplicate["id"])

                connection.execute(
                    """
                    INSERT INTO documents (
                        id, content_hash, original_filename, stored_path,
                        document_format, size_bytes, title, page_count,
                        chunk_count, extractor_name, extractor_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        document.source.content_hash,
                        document.source.filename,
                        str(relative_path),
                        document.source.format.value,
                        document.source.size_bytes,
                        document.title,
                        document.page_count,
                        len(chunks),
                        document.extractor_name,
                        document.extractor_version,
                        created_at.isoformat(),
                    ),
                )
                chunk_rows = [self._chunk_values(document_id, chunk) for chunk in chunks]
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        id, document_id, sequence, text, character_count,
                        page_start, page_end, headings, line_start, line_end
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunk_rows,
                )
                chunk_ids = {row[2]: row[0] for row in chunk_rows}
                connection.executemany(
                    """
                    INSERT INTO embeddings (
                        chunk_id, model, dimensions, vector, magnitude, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        _embedding_values(chunk_ids[embedding.sequence], embedding)
                        for embedding in embeddings
                    ],
                )
                temporary_directory.rename(final_directory)
                moved_to_final = True
            committed = True
        except DuplicateDocumentError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                f"Document could not be saved locally: {document.source.filename}",
            ) from error
        finally:
            if temporary_directory.exists():
                shutil.rmtree(temporary_directory, ignore_errors=True)
            if moved_to_final and not committed:
                shutil.rmtree(final_directory, ignore_errors=True)

        stored = self.get_document(document_id)
        if stored is None:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Document storage completed without a readable document record",
            )
        return stored

    def missing_embedding_chunks(
        self,
        document_id: str,
        *,
        model: str,
        dimensions: int,
    ) -> tuple[StoredChunk, ...]:
        """Return chunks without a vector for one model configuration."""

        self.initialise()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT chunks.*
                FROM chunks
                LEFT JOIN embeddings
                  ON embeddings.chunk_id = chunks.id
                 AND embeddings.model = ?
                 AND embeddings.dimensions = ?
                WHERE chunks.document_id = ? AND embeddings.chunk_id IS NULL
                ORDER BY chunks.sequence
                """,
                (model, dimensions, document_id),
            ).fetchall()
        return tuple(self._chunk_from_row(row) for row in rows)

    def save_embeddings(
        self,
        document_id: str,
        embeddings: Sequence[PreparedEmbedding],
    ) -> None:
        """Atomically add or replace vectors for existing document chunks."""

        if not embeddings:
            return
        self.initialise()
        chunks = self.list_chunks(document_id)
        prepared_chunks = tuple(
            PreparedChunk(
                sequence=chunk.sequence,
                text=chunk.text,
                character_count=chunk.character_count,
                location=chunk.location,
            )
            for chunk in chunks
        )
        _validate_prepared_embeddings(
            prepared_chunks,
            embeddings,
            require_complete=False,
        )
        chunk_ids = {chunk.sequence: chunk.id for chunk in chunks}
        try:
            with self._connection() as connection:
                connection.executemany(
                    """
                    INSERT INTO embeddings (
                        chunk_id, model, dimensions, vector, magnitude, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, model, dimensions) DO UPDATE SET
                        vector = excluded.vector,
                        magnitude = excluded.magnitude,
                        created_at = excluded.created_at
                    """,
                    [
                        _embedding_values(chunk_ids[embedding.sequence], embedding)
                        for embedding in embeddings
                    ],
                )
        except (KeyError, sqlite3.Error) as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Chunk embeddings could not be saved locally",
            ) from error

    def list_embeddings(
        self,
        document_id: str,
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> tuple[StoredEmbedding, ...]:
        """Return vectors stored for one document."""

        self.initialise()
        query = """
            SELECT embeddings.*
            FROM embeddings
            JOIN chunks ON chunks.id = embeddings.chunk_id
            WHERE chunks.document_id = ?
        """
        parameters: list[object] = [document_id]
        if model is not None:
            query += " AND embeddings.model = ?"
            parameters.append(model)
        if dimensions is not None:
            query += " AND embeddings.dimensions = ?"
            parameters.append(dimensions)
        query += " ORDER BY chunks.sequence"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_embedding_from_row(row) for row in rows)

    def search_vectors(
        self,
        query_vector: Sequence[float],
        *,
        model: str,
        limit: int = 5,
        document_id: str | None = None,
    ) -> tuple[VectorSearchResult, ...]:
        """Rank locally stored vectors with cosine similarity."""

        if limit < 1:
            raise ValueError("limit must be greater than zero")
        vector = tuple(float(value) for value in query_vector)
        query_magnitude = _vector_magnitude(vector)
        self.initialise()
        query = """
            SELECT embeddings.*, chunks.*
            FROM embeddings
            JOIN chunks ON chunks.id = embeddings.chunk_id
            WHERE embeddings.model = ? AND embeddings.dimensions = ?
        """
        parameters: list[object] = [model, len(vector)]
        if document_id is not None:
            query += " AND chunks.document_id = ?"
            parameters.append(document_id)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        ranked: list[tuple[float, StoredChunk]] = []
        for row in rows:
            stored_vector = _unpack_vector(row["vector"], row["dimensions"])
            score = sum(
                query_value * stored_value
                for query_value, stored_value in zip(vector, stored_vector, strict=True)
            ) / (query_magnitude * row["magnitude"])
            ranked.append((score, self._chunk_from_row(row)))
        ranked.sort(key=lambda item: (-item[0], item[1].id))

        documents: dict[str, StoredDocument] = {}
        results: list[VectorSearchResult] = []
        for score, chunk in ranked[:limit]:
            if chunk.document_id not in documents:
                document = self.get_document(chunk.document_id)
                if document is None:
                    continue
                documents[chunk.document_id] = document
            results.append(
                VectorSearchResult(
                    score=score,
                    model=model,
                    chunk=chunk,
                    document=documents[chunk.document_id],
                )
            )
        return tuple(results)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _document_from_row(self, row: sqlite3.Row) -> StoredDocument:
        return StoredDocument(
            id=row["id"],
            content_hash=row["content_hash"],
            original_filename=row["original_filename"],
            stored_path=self.data_directory / row["stored_path"],
            format=DocumentFormat(row["document_format"]),
            size_bytes=row["size_bytes"],
            title=row["title"],
            page_count=row["page_count"],
            chunk_count=row["chunk_count"],
            extractor_name=row["extractor_name"],
            extractor_version=row["extractor_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _chunk_values(document_id: str, chunk: PreparedChunk) -> tuple[object, ...]:
        return (
            str(uuid4()),
            document_id,
            chunk.sequence,
            chunk.text,
            chunk.character_count,
            chunk.location.page_start,
            chunk.location.page_end,
            json.dumps(chunk.location.headings),
            chunk.location.line_start,
            chunk.location.line_end,
        )

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row) -> StoredChunk:
        return StoredChunk(
            id=row["id"],
            document_id=row["document_id"],
            sequence=row["sequence"],
            text=row["text"],
            character_count=row["character_count"],
            location=ChunkLocation(
                page_start=row["page_start"],
                page_end=row["page_end"],
                headings=tuple(json.loads(row["headings"])),
                line_start=row["line_start"],
                line_end=row["line_end"],
            ),
        )


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _validate_prepared_embeddings(
    chunks: Sequence[PreparedChunk],
    embeddings: Sequence[PreparedEmbedding],
    *,
    require_complete: bool,
) -> None:
    if not embeddings:
        return
    chunk_sequences = {chunk.sequence for chunk in chunks}
    embedding_sequences = [embedding.sequence for embedding in embeddings]
    if len(embedding_sequences) != len(set(embedding_sequences)):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Only one embedding per chunk sequence can be saved at a time",
        )
    if not set(embedding_sequences).issubset(chunk_sequences):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "An embedding does not match a document chunk",
        )
    if require_complete and set(embedding_sequences) != chunk_sequences:
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Embedded ingestion requires one vector for every chunk",
        )
    configurations = {(item.model, item.dimensions) for item in embeddings}
    if len(configurations) != 1:
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "One embedding operation must use a single model configuration",
        )
    for embedding in embeddings:
        if not embedding.model.strip():
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Embedding model must not be empty",
            )
        if len(embedding.vector) != embedding.dimensions:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Embedding dimensions do not match the stored vector",
            )
        _vector_magnitude(embedding.vector)


def _embedding_values(
    chunk_id: str,
    embedding: PreparedEmbedding,
) -> tuple[object, ...]:
    packed_vector = _pack_vector(embedding.vector)
    stored_vector = _unpack_vector(packed_vector, embedding.dimensions)
    return (
        chunk_id,
        embedding.model,
        embedding.dimensions,
        packed_vector,
        _vector_magnitude(stored_vector),
        datetime.now(UTC).isoformat(),
    )


def _vector_magnitude(vector: Sequence[float]) -> float:
    if not vector or any(not isfinite(value) for value in vector):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Embedding vector must contain finite values",
        )
    magnitude = sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Embedding vector must not be all zeros",
        )
    return magnitude


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(value: bytes, dimensions: int) -> tuple[float, ...]:
    expected_size = dimensions * 4
    if len(value) != expected_size:
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Stored embedding vector has an invalid size",
        )
    return struct.unpack(f"<{dimensions}f", value)


def _embedding_from_row(row: sqlite3.Row) -> StoredEmbedding:
    return StoredEmbedding(
        chunk_id=row["chunk_id"],
        model=row["model"],
        dimensions=row["dimensions"],
        vector=_unpack_vector(row["vector"], row["dimensions"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
