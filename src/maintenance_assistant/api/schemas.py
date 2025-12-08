"""Public request and response models for the application API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from maintenance_assistant.ingestion import (
    IngestionResult,
    StoredChunk,
    StoredDocument,
    VectorSearchResult,
)


class ErrorDetail(BaseModel):
    """A stable machine code and safe explanation."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Envelope used for expected API failures."""

    error: ErrorDetail


class HealthResponse(BaseModel):
    """Readiness information for the local API."""

    status: str
    storage: str
    embeddings: str
    embedding_model: str | None


class DocumentResponse(BaseModel):
    """Safe document metadata that excludes local paths and content hashes."""

    id: str
    original_filename: str
    format: str
    size_bytes: int
    title: str
    page_count: int | None
    chunk_count: int
    extractor_name: str
    extractor_version: str
    created_at: datetime

    @classmethod
    def from_document(cls, document: StoredDocument) -> DocumentResponse:
        """Build a response from a stored document record."""

        return cls(
            id=document.id,
            original_filename=document.original_filename,
            format=document.format.value,
            size_bytes=document.size_bytes,
            title=document.title,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            extractor_name=document.extractor_name,
            extractor_version=document.extractor_version,
            created_at=document.created_at,
        )


class EmbeddingResponse(BaseModel):
    """Embedding work completed during ingestion."""

    chunk_count: int
    model: str | None
    input_tokens: int


class IngestionResponse(BaseModel):
    """Outcome of uploading and ingesting one document."""

    status: str
    document: DocumentResponse
    embeddings: EmbeddingResponse

    @classmethod
    def from_result(cls, result: IngestionResult) -> IngestionResponse:
        """Build a response from the ingestion domain result."""

        return cls(
            status=result.status.value,
            document=DocumentResponse.from_document(result.document),
            embeddings=EmbeddingResponse(
                chunk_count=result.embedded_chunk_count,
                model=result.embedding_model,
                input_tokens=result.embedding_input_tokens,
            ),
        )


class DocumentListResponse(BaseModel):
    """One page of stored document metadata."""

    items: list[DocumentResponse]
    limit: int
    offset: int


class SearchRequest(BaseModel):
    """A semantic query and optional document filter."""

    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=50)
    document_id: str | None = Field(default=None, min_length=1)

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        """Reject whitespace-only queries and remove accidental padding."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("query must contain text")
        return stripped


class ChunkLocationResponse(BaseModel):
    """Traceable source location for a search result."""

    page_start: int | None
    page_end: int | None
    headings: list[str]
    line_start: int | None
    line_end: int | None


class ChunkResponse(BaseModel):
    """A stored text chunk returned by semantic search."""

    id: str
    document_id: str
    sequence: int
    text: str
    character_count: int
    location: ChunkLocationResponse

    @classmethod
    def from_chunk(cls, chunk: StoredChunk) -> ChunkResponse:
        """Build a response from a stored chunk."""

        return cls(
            id=chunk.id,
            document_id=chunk.document_id,
            sequence=chunk.sequence,
            text=chunk.text,
            character_count=chunk.character_count,
            location=ChunkLocationResponse(
                page_start=chunk.location.page_start,
                page_end=chunk.location.page_end,
                headings=list(chunk.location.headings),
                line_start=chunk.location.line_start,
                line_end=chunk.location.line_end,
            ),
        )


class SearchResultResponse(BaseModel):
    """One semantically ranked chunk and its document metadata."""

    score: float
    model: str
    document: DocumentResponse
    chunk: ChunkResponse

    @classmethod
    def from_result(cls, result: VectorSearchResult) -> SearchResultResponse:
        """Build a response from a vector search result."""

        return cls(
            score=result.score,
            model=result.model,
            document=DocumentResponse.from_document(result.document),
            chunk=ChunkResponse.from_chunk(result.chunk),
        )


class SearchResponse(BaseModel):
    """Ranked local semantic-search results."""

    results: list[SearchResultResponse]
