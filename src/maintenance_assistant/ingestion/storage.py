"""Transactional SQLite and file storage for ingested documents."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isfinite, sqrt
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import tempfile
from typing import Mapping, Sequence
from uuid import uuid4

from maintenance_assistant.ingestion.errors import (
    DocumentLifecycleError,
    DocumentLifecycleErrorCode,
    DuplicateDocumentError,
    IngestionError,
    IngestionErrorCode,
)
from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    DocumentLifecycleStatus,
    DocumentFormat,
    ExtractedDocument,
    LexicalSearchResult,
    PreparedChunk,
    PreparedEmbedding,
    PreparedParentChunk,
    StoredChunk,
    StoredDocument,
    StoredEmbedding,
    StoredParentChunk,
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

_MIGRATION_VERSION_3 = """
ALTER TABLE documents
ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'current'
CHECK (lifecycle_status IN ('current', 'superseded', 'archived'));

ALTER TABLE documents
ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0);

ALTER TABLE documents
ADD COLUMN supersedes_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL;

ALTER TABLE documents
ADD COLUMN lifecycle_updated_at TEXT;

UPDATE documents SET lifecycle_updated_at = created_at
WHERE lifecycle_updated_at IS NULL;

CREATE INDEX IF NOT EXISTS documents_lifecycle_status_idx
ON documents(lifecycle_status);

CREATE INDEX IF NOT EXISTS documents_supersedes_idx
ON documents(supersedes_document_id);
"""

_MIGRATION_VERSION_4 = """
ALTER TABLE chunks
ADD COLUMN token_count INTEGER
CHECK (token_count IS NULL OR token_count > 0);
"""

_MIGRATION_VERSION_5 = """
CREATE TABLE IF NOT EXISTS parent_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    text TEXT NOT NULL CHECK (length(text) > 0),
    character_count INTEGER NOT NULL CHECK (character_count > 0),
    token_count INTEGER NOT NULL CHECK (token_count > 0),
    page_start INTEGER,
    page_end INTEGER,
    headings TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    UNIQUE (document_id, sequence)
);

CREATE INDEX IF NOT EXISTS parent_chunks_document_id_idx
ON parent_chunks(document_id);

ALTER TABLE chunks
ADD COLUMN parent_id TEXT REFERENCES parent_chunks(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS chunks_parent_id_idx ON chunks(parent_id);
"""

_MIGRATION_VERSION_6 = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);

INSERT OR REPLACE INTO chunk_fts(rowid, text)
SELECT rowid, text FROM chunks;

CREATE TRIGGER IF NOT EXISTS chunks_fts_insert
AFTER INSERT ON chunks BEGIN
    INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_delete
AFTER DELETE ON chunks BEGIN
    DELETE FROM chunk_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_update
AFTER UPDATE OF text ON chunks BEGIN
    DELETE FROM chunk_fts WHERE rowid = old.rowid;
    INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

_MIGRATION_VERSION_7 = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK (length(title) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(content) > 0),
    created_at TEXT NOT NULL,
    scope_document_id TEXT,
    answerable INTEGER CHECK (answerable IS NULL OR answerable IN (0, 1)),
    model TEXT,
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    citations_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (conversation_id, sequence)
);

CREATE INDEX IF NOT EXISTS conversations_updated_at_idx
ON conversations(updated_at DESC);

CREATE INDEX IF NOT EXISTS conversation_messages_conversation_idx
ON conversation_messages(conversation_id, sequence);
"""

_MIGRATION_VERSION_8 = """
CREATE TABLE IF NOT EXISTS conversation_message_feedback (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL UNIQUE
        REFERENCES conversation_messages(id) ON DELETE CASCADE,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS conversation_feedback_conversation_idx
ON conversation_message_feedback(conversation_id);
"""

_CURRENT_SCHEMA_VERSION = 8


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
                if version == 2:
                    connection.executescript(_MIGRATION_VERSION_3)
                    connection.execute("PRAGMA user_version = 3")
                    version = 3
                if version == 3:
                    connection.executescript(_MIGRATION_VERSION_4)
                    connection.execute("PRAGMA user_version = 4")
                    version = 4
                if version == 4:
                    connection.executescript(_MIGRATION_VERSION_5)
                    connection.execute("PRAGMA user_version = 5")
                    version = 5
                if version == 5:
                    connection.executescript(_MIGRATION_VERSION_6)
                    connection.execute("PRAGMA user_version = 6")
                    version = 6
                if version == 6:
                    connection.executescript(_MIGRATION_VERSION_7)
                    connection.execute("PRAGMA user_version = 7")
                    version = 7
                if version == 7:
                    connection.executescript(_MIGRATION_VERSION_8)
                    connection.execute("PRAGMA user_version = 8")
                    version = 8
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

    def list_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        lifecycle_status: DocumentLifecycleStatus | None = None,
    ) -> tuple[StoredDocument, ...]:
        """Return stored documents from newest to oldest."""

        if limit < 1:
            raise ValueError("limit must be greater than zero")
        if offset < 0:
            raise ValueError("offset must be zero or greater")
        self.initialise()
        try:
            with self._connection() as connection:
                query = "SELECT * FROM documents"
                parameters: list[object] = []
                if lifecycle_status is not None:
                    query += " WHERE lifecycle_status = ?"
                    parameters.append(lifecycle_status.value)
                query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
                parameters.extend((limit, offset))
                rows = connection.execute(query, parameters).fetchall()
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Local document storage could not be queried",
            ) from error
        return tuple(self._document_from_row(row) for row in rows)

    def list_chunks(self, document_id: str) -> tuple[StoredChunk, ...]:
        """Return stored chunks in document order."""

        self.initialise()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM chunks WHERE document_id = ? ORDER BY sequence",
                (document_id,),
            ).fetchall()
        return tuple(self._chunk_from_row(row) for row in rows)

    def list_parent_chunks(self, document_id: str) -> tuple[StoredParentChunk, ...]:
        """Return stored parent context sections in document order."""

        self.initialise()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM parent_chunks WHERE document_id = ? ORDER BY sequence",
                (document_id,),
            ).fetchall()
        return tuple(self._parent_chunk_from_row(row) for row in rows)

    def get_parent_chunk(self, parent_id: str) -> StoredParentChunk | None:
        """Return one stored parent context section by identifier."""

        self.initialise()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM parent_chunks WHERE id = ?", (parent_id,)
            ).fetchone()
        return self._parent_chunk_from_row(row) if row else None

    def list_revision_history(self, document_id: str) -> tuple[StoredDocument, ...]:
        """Return the complete oldest-to-newest revision chain for one manual."""

        self.initialise()
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM documents").fetchall()
        documents = {row["id"]: row for row in rows}
        selected = documents.get(document_id)
        if selected is None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                "Document was not found",
            )

        root_id = selected["id"]
        visited: set[str] = set()
        while documents[root_id]["supersedes_document_id"] is not None:
            if root_id in visited:
                raise IngestionError(
                    IngestionErrorCode.STORAGE_FAILED,
                    "Stored manual revision history contains a cycle",
                )
            visited.add(root_id)
            root_id = documents[root_id]["supersedes_document_id"]

        chain: list[sqlite3.Row] = []
        next_id: str | None = root_id
        while next_id is not None:
            row = documents[next_id]
            chain.append(row)
            children = [
                item
                for item in rows
                if item["supersedes_document_id"] == next_id
            ]
            if len(children) > 1:
                raise IngestionError(
                    IngestionErrorCode.STORAGE_FAILED,
                    "Stored manual revision history contains conflicting revisions",
                )
            next_id = children[0]["id"] if children else None
        return tuple(self._document_from_row(row) for row in chain)

    def archive_document(self, document_id: str) -> StoredDocument:
        """Archive a manual so it cannot contribute evidence to new answers."""

        self.initialise()
        updated_at = datetime.now(UTC)
        with self._connection() as connection:
            changed = connection.execute(
                """
                UPDATE documents
                SET lifecycle_status = ?, lifecycle_updated_at = ?
                WHERE id = ? AND lifecycle_status != ?
                """,
                (
                    DocumentLifecycleStatus.ARCHIVED.value,
                    updated_at.isoformat(),
                    document_id,
                    DocumentLifecycleStatus.ARCHIVED.value,
                ),
            ).rowcount
            exists = changed or connection.execute(
                "SELECT 1 FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        if not exists:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                "Document was not found",
            )
        stored = self.get_document(document_id)
        if stored is None:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Archived document could not be read",
            )
        return stored

    def delete_document(self, document_id: str) -> None:
        """Permanently remove one manual, its chunks, vectors and stored file."""

        document = self.get_document(document_id)
        if document is None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                "Document was not found",
            )

        document_directory = document.stored_path.parent
        staged_directory = self.documents_directory / f".deleting-{document_id}-{uuid4()}"
        staged = False
        committed = False
        try:
            if document_directory.exists():
                document_directory.rename(staged_directory)
                staged = True
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                deleted = connection.execute(
                    "DELETE FROM documents WHERE id = ?", (document_id,)
                ).rowcount
                if deleted != 1:
                    raise DocumentLifecycleError(
                        DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                        "Document was not found",
                    )
            committed = True
        except DocumentLifecycleError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Document could not be deleted from local storage",
            ) from error
        finally:
            if staged and not committed and staged_directory.exists():
                staged_directory.rename(document_directory)
            if staged and committed:
                shutil.rmtree(staged_directory, ignore_errors=True)

    def save(
        self,
        document: ExtractedDocument,
        chunks: Sequence[PreparedChunk],
        embeddings: Sequence[PreparedEmbedding] = (),
        *,
        parents: Sequence[PreparedParentChunk] = (),
        supersedes_document_id: str | None = None,
    ) -> StoredDocument:
        """Atomically store a fully prepared document and its chunks."""

        if not chunks:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "A document must contain at least one chunk",
            )
        _validate_prepared_embeddings(chunks, embeddings, require_complete=True)
        _validate_chunk_hierarchy(parents, chunks)
        self.initialise()
        document_id = str(uuid4())
        created_at = datetime.now(UTC)
        revision = 1
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

                if supersedes_document_id is not None:
                    previous = connection.execute(
                        "SELECT lifecycle_status, revision FROM documents WHERE id = ?",
                        (supersedes_document_id,),
                    ).fetchone()
                    if previous is None:
                        raise DocumentLifecycleError(
                            DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                            "The manual being replaced was not found",
                        )
                    if previous["lifecycle_status"] != DocumentLifecycleStatus.CURRENT:
                        raise DocumentLifecycleError(
                            DocumentLifecycleErrorCode.REVISION_CONFLICT,
                            "Only a current manual can be replaced",
                        )
                    revision = previous["revision"] + 1

                connection.execute(
                    """
                    INSERT INTO documents (
                        id, content_hash, original_filename, stored_path,
                        document_format, size_bytes, title, page_count,
                        chunk_count, extractor_name, extractor_version, created_at,
                        lifecycle_status, revision, supersedes_document_id,
                        lifecycle_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        DocumentLifecycleStatus.CURRENT.value,
                        revision,
                        supersedes_document_id,
                        created_at.isoformat(),
                    ),
                )
                parent_rows = [
                    self._parent_chunk_values(document_id, parent) for parent in parents
                ]
                connection.executemany(
                    """
                    INSERT INTO parent_chunks (
                        id, document_id, sequence, text, character_count,
                        token_count, page_start, page_end, headings,
                        line_start, line_end
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    parent_rows,
                )
                parent_ids = {row[2]: row[0] for row in parent_rows}
                chunk_rows = [
                    self._chunk_values(document_id, chunk, parent_ids)
                    for chunk in chunks
                ]
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        id, document_id, sequence, text, character_count,
                        page_start, page_end, headings, line_start, line_end,
                        token_count, parent_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                if supersedes_document_id is not None:
                    connection.execute(
                        """
                        UPDATE documents
                        SET lifecycle_status = ?, lifecycle_updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            DocumentLifecycleStatus.SUPERSEDED.value,
                            created_at.isoformat(),
                            supersedes_document_id,
                        ),
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

    def replace_chunks(
        self,
        document_id: str,
        extracted: ExtractedDocument,
        parents: Sequence[PreparedParentChunk],
        chunks: Sequence[PreparedChunk],
        embeddings: Sequence[PreparedEmbedding],
    ) -> StoredDocument:
        """Atomically replace one manual's parsed hierarchy and vectors."""

        if not chunks:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "A document must contain at least one chunk",
            )
        _validate_chunk_hierarchy(parents, chunks)
        _validate_prepared_embeddings(chunks, embeddings, require_complete=True)
        self.initialise()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                exists = connection.execute(
                    "SELECT 1 FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if exists is None:
                    raise DocumentLifecycleError(
                        DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                        "Document was not found",
                    )
                connection.execute(
                    "DELETE FROM chunks WHERE document_id = ?", (document_id,)
                )
                connection.execute(
                    "DELETE FROM parent_chunks WHERE document_id = ?", (document_id,)
                )
                parent_rows = [
                    self._parent_chunk_values(document_id, parent) for parent in parents
                ]
                connection.executemany(
                    """
                    INSERT INTO parent_chunks (
                        id, document_id, sequence, text, character_count,
                        token_count, page_start, page_end, headings,
                        line_start, line_end
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    parent_rows,
                )
                parent_ids = {row[2]: row[0] for row in parent_rows}
                chunk_rows = [
                    self._chunk_values(document_id, chunk, parent_ids)
                    for chunk in chunks
                ]
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        id, document_id, sequence, text, character_count,
                        page_start, page_end, headings, line_start, line_end,
                        token_count, parent_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        _embedding_values(chunk_ids[item.sequence], item)
                        for item in embeddings
                    ],
                )
                connection.execute(
                    """
                    UPDATE documents
                    SET page_count = ?, chunk_count = ?,
                        extractor_name = ?, extractor_version = ?
                    WHERE id = ?
                    """,
                    (
                        extracted.page_count,
                        len(chunks),
                        extracted.extractor_name,
                        extracted.extractor_version,
                        document_id,
                    ),
                )
        except DocumentLifecycleError:
            raise
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Document chunks could not be replaced locally",
            ) from error
        stored = self.get_document(document_id)
        if stored is None:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Re-indexed document could not be read",
            )
        return stored

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
                token_count=chunk.token_count,
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
            JOIN documents ON documents.id = chunks.document_id
            WHERE embeddings.model = ? AND embeddings.dimensions = ?
              AND documents.lifecycle_status = 'current'
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
        parents: dict[str, StoredParentChunk | None] = {}
        results: list[VectorSearchResult] = []
        for score, chunk in ranked[:limit]:
            if chunk.document_id not in documents:
                document = self.get_document(chunk.document_id)
                if document is None:
                    continue
                documents[chunk.document_id] = document
            parent = None
            if chunk.parent_id is not None:
                if chunk.parent_id not in parents:
                    parents[chunk.parent_id] = self.get_parent_chunk(chunk.parent_id)
                parent = parents[chunk.parent_id]
            results.append(
                VectorSearchResult(
                    score=score,
                    model=model,
                    chunk=chunk,
                    document=documents[chunk.document_id],
                    parent=parent,
                    semantic_score=score,
                    retrieval_methods=("semantic",),
                )
            )
        return tuple(results)

    def search_text(
        self,
        query_text: str,
        *,
        limit: int = 20,
        document_id: str | None = None,
    ) -> tuple[LexicalSearchResult, ...]:
        """Rank current manual chunks with SQLite full-text search."""

        if limit < 1:
            raise ValueError("limit must be greater than zero")
        expression = _fts_expression(query_text)
        if not expression:
            return ()
        self.initialise()
        query = """
            SELECT chunks.*, bm25(chunk_fts) AS text_score
            FROM chunk_fts
            JOIN chunks ON chunks.rowid = chunk_fts.rowid
            JOIN documents ON documents.id = chunks.document_id
            WHERE chunk_fts MATCH ?
              AND documents.lifecycle_status = 'current'
        """
        parameters: list[object] = [expression]
        if document_id is not None:
            query += " AND chunks.document_id = ?"
            parameters.append(document_id)
        query += " ORDER BY text_score, chunks.id LIMIT ?"
        parameters.append(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(query, parameters).fetchall()
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Local full-text search could not be completed",
            ) from error

        documents: dict[str, StoredDocument] = {}
        parents: dict[str, StoredParentChunk | None] = {}
        results: list[LexicalSearchResult] = []
        for row in rows:
            chunk = self._chunk_from_row(row)
            if chunk.document_id not in documents:
                document = self.get_document(chunk.document_id)
                if document is None:
                    continue
                documents[chunk.document_id] = document
            parent = None
            if chunk.parent_id is not None:
                if chunk.parent_id not in parents:
                    parents[chunk.parent_id] = self.get_parent_chunk(chunk.parent_id)
                parent = parents[chunk.parent_id]
            results.append(
                LexicalSearchResult(
                    score=-float(row["text_score"]),
                    chunk=chunk,
                    document=documents[chunk.document_id],
                    parent=parent,
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
            lifecycle_status=DocumentLifecycleStatus(row["lifecycle_status"]),
            revision=row["revision"],
            supersedes_document_id=row["supersedes_document_id"],
            lifecycle_updated_at=datetime.fromisoformat(
                row["lifecycle_updated_at"] or row["created_at"]
            ),
        )

    @staticmethod
    def _chunk_values(
        document_id: str,
        chunk: PreparedChunk,
        parent_ids: Mapping[int, str] | None = None,
    ) -> tuple[object, ...]:
        parents = parent_ids or {}
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
            chunk.token_count,
            parents.get(chunk.parent_sequence),
        )

    @staticmethod
    def _parent_chunk_values(
        document_id: str,
        parent: PreparedParentChunk,
    ) -> tuple[object, ...]:
        return (
            str(uuid4()),
            document_id,
            parent.sequence,
            parent.text,
            parent.character_count,
            parent.token_count,
            parent.location.page_start,
            parent.location.page_end,
            json.dumps(parent.location.headings),
            parent.location.line_start,
            parent.location.line_end,
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
            token_count=row["token_count"],
            parent_id=row["parent_id"],
        )

    @staticmethod
    def _parent_chunk_from_row(row: sqlite3.Row) -> StoredParentChunk:
        return StoredParentChunk(
            id=row["id"],
            document_id=row["document_id"],
            sequence=row["sequence"],
            text=row["text"],
            character_count=row["character_count"],
            token_count=row["token_count"],
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


def _fts_expression(query_text: str) -> str:
    fragments = re.findall(
        r"[^\W_]+(?:[-./][^\W_]+)*",
        query_text.casefold(),
        flags=re.UNICODE,
    )
    phrases = []
    for fragment in fragments:
        tokens = re.findall(r"[^\W_]+", fragment, flags=re.UNICODE)
        if tokens:
            phrases.append(f'"{" ".join(tokens)}"')
    return " OR ".join(dict.fromkeys(phrases))


def _validate_chunk_hierarchy(
    parents: Sequence[PreparedParentChunk],
    chunks: Sequence[PreparedChunk],
) -> None:
    parent_sequences = [parent.sequence for parent in parents]
    if len(parent_sequences) != len(set(parent_sequences)):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Parent chunk sequences must be unique",
        )
    available = set(parent_sequences)
    referenced = {
        chunk.parent_sequence
        for chunk in chunks
        if chunk.parent_sequence is not None
    }
    if not referenced.issubset(available):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "A child chunk refers to an unavailable parent",
        )
    if parents and any(chunk.parent_sequence is None for chunk in chunks):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Every child chunk must refer to a parent",
        )
    if parents and referenced != available:
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Every parent chunk must contain a child",
        )
    if parents and any(
        chunk.token_count is None
        or chunk.token_count < 1
        or chunk.character_count != len(chunk.text)
        for chunk in chunks
    ):
        raise IngestionError(
            IngestionErrorCode.STORAGE_FAILED,
            "Child chunk metadata is invalid",
        )
    for parent in parents:
        if (
            parent.sequence < 0
            or parent.token_count < 1
            or not parent.text.strip()
            or parent.character_count != len(parent.text)
        ):
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Parent chunk metadata is invalid",
            )


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
