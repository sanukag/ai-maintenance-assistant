"""Domain values shared by document-ingestion stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class DocumentFormat(StrEnum):
    """Document formats supported by the initial ingestion pipeline."""

    PDF = "pdf"
    TEXT = "text"
    MARKDOWN = "markdown"


class IngestionStatus(StrEnum):
    """Successful outcomes returned by the ingestion service."""

    COMPLETED = "completed"
    ALREADY_EXISTS = "already_exists"


class DocumentLifecycleStatus(StrEnum):
    """Whether a stored manual may contribute evidence to new answers."""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ValidatedDocument:
    """A local document that is safe to pass to an extractor."""

    path: Path
    filename: str
    format: DocumentFormat
    size_bytes: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """The human-readable location of text within its source document."""

    page_number: int | None = None
    heading: str | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    """A structural unit produced by a document extractor."""

    text: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Text and source structure extracted from a validated document."""

    source: ValidatedDocument
    title: str
    segments: tuple[ExtractedSegment, ...]
    page_count: int | None = None
    extractor_name: str = "built-in"
    extractor_version: str = "1"


@dataclass(frozen=True, slots=True)
class NormalisedSegment:
    """A cleaned structural unit with its original source location."""

    text: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class NormalisedDocument:
    """Conservatively cleaned text ready for chunking."""

    extracted: ExtractedDocument
    segments: tuple[NormalisedSegment, ...]


@dataclass(frozen=True, slots=True)
class ChunkLocation:
    """The source range represented by a prepared chunk."""

    page_start: int | None = None
    page_end: int | None = None
    headings: tuple[str, ...] = ()
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    """A traceable piece of document content ready for storage."""

    sequence: int
    text: str
    character_count: int
    location: ChunkLocation
    token_count: int | None = None
    parent_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class PreparedParentChunk:
    """A larger section of source context containing retrieval children."""

    sequence: int
    text: str
    character_count: int
    token_count: int
    location: ChunkLocation


@dataclass(frozen=True, slots=True)
class PreparedChunkHierarchy:
    """Parent context and child retrieval chunks prepared together."""

    parents: tuple[PreparedParentChunk, ...]
    children: tuple[PreparedChunk, ...]


@dataclass(frozen=True, slots=True)
class PreparedEmbedding:
    """A vector associated with a prepared chunk sequence."""

    sequence: int
    model: str
    dimensions: int
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """A document record committed to local storage."""

    id: str
    content_hash: str
    original_filename: str
    stored_path: Path
    format: DocumentFormat
    size_bytes: int
    title: str
    page_count: int | None
    chunk_count: int
    extractor_name: str
    extractor_version: str
    created_at: datetime
    lifecycle_status: DocumentLifecycleStatus = DocumentLifecycleStatus.CURRENT
    revision: int = 1
    supersedes_document_id: str | None = None
    lifecycle_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """A locally persisted chunk and its source range."""

    id: str
    document_id: str
    sequence: int
    text: str
    character_count: int
    location: ChunkLocation
    token_count: int | None = None
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoredParentChunk:
    """A persisted section used to expand retrieved child context."""

    id: str
    document_id: str
    sequence: int
    text: str
    character_count: int
    token_count: int
    location: ChunkLocation


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    """A vector persisted for a stored chunk."""

    chunk_id: str
    model: str
    dimensions: int
    vector: tuple[float, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    """A stored chunk ranked against an embedded query."""

    score: float
    model: str
    chunk: StoredChunk
    document: StoredDocument
    parent: StoredParentChunk | None = None
    semantic_score: float | None = None
    lexical_score: float | None = None
    retrieval_methods: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LexicalSearchResult:
    """A stored chunk ranked by SQLite full-text relevance."""

    score: float
    chunk: StoredChunk
    document: StoredDocument
    parent: StoredParentChunk | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """The successful outcome of an end-to-end ingestion request."""

    status: IngestionStatus
    document: StoredDocument
    embedded_chunk_count: int = 0
    embedding_model: str | None = None
    embedding_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ReindexResult:
    """Embedding work completed for every chunk in one stored manual."""

    document: StoredDocument
    embedded_chunk_count: int
    embedding_model: str
    embedding_input_tokens: int
