"""Transactional SQLite and file storage for ingested documents."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
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
    StoredChunk,
    StoredDocument,
)

_SCHEMA = """
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
PRAGMA user_version = 1;
"""


class LocalDocumentStore:
    """Persist original files, document metadata and chunks under one data root."""

    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory.expanduser().resolve()
        self.database_path = self.data_directory / "maintenance-assistant.db"
        self.documents_directory = self.data_directory / "documents"

    def initialise(self) -> None:
        """Create local directories and the version-one database schema."""

        self.documents_directory.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection() as connection:
                connection.executescript(_SCHEMA)
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
    ) -> StoredDocument:
        """Atomically store a fully prepared document and its chunks."""

        if not chunks:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "A document must contain at least one chunk",
            )
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
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        id, document_id, sequence, text, character_count,
                        page_start, page_end, headings, line_start, line_end
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._chunk_values(document_id, chunk) for chunk in chunks],
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
