"""Public request and response models for the application API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from maintenance_assistant.answering import AnswerCitation, GroundedAnswer
from maintenance_assistant.ingestion import (
    DocumentLifecycleStatus,
    IngestionResult,
    ReindexResult,
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
    answers: str
    answer_model: str | None


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
    lifecycle_status: DocumentLifecycleStatus
    revision: int
    supersedes_document_id: str | None
    lifecycle_updated_at: datetime

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
            lifecycle_status=document.lifecycle_status,
            revision=document.revision,
            supersedes_document_id=document.supersedes_document_id,
            lifecycle_updated_at=document.lifecycle_updated_at or document.created_at,
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


class RevisionHistoryResponse(BaseModel):
    """The ordered revision history for one manual family."""

    items: list[DocumentResponse]


class ReindexResponse(BaseModel):
    """Embedding work completed when re-indexing a stored manual."""

    document: DocumentResponse
    embeddings: EmbeddingResponse

    @classmethod
    def from_result(cls, result: ReindexResult) -> ReindexResponse:
        """Build a response from a complete re-indexing result."""

        return cls(
            document=DocumentResponse.from_document(result.document),
            embeddings=EmbeddingResponse(
                chunk_count=result.embedded_chunk_count,
                model=result.embedding_model,
                input_tokens=result.embedding_input_tokens,
            ),
        )


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
    token_count: int | None
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
            token_count=chunk.token_count,
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


class AnswerRequest(BaseModel):
    """A question, evidence limit and optional document filter."""

    question: str = Field(min_length=1, max_length=2_000)
    max_sources: int = Field(default=5, ge=1, le=10)
    document_id: str | None = Field(default=None, min_length=1)

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        """Reject whitespace-only questions and remove accidental padding."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("question must contain text")
        return stripped


class AnswerCitationResponse(BaseModel):
    """A citation to one exact stored chunk and its source location."""

    source_id: str
    score: float
    document: DocumentResponse
    chunk_id: str
    chunk_sequence: int
    excerpt: str
    page_start: int | None
    page_end: int | None
    headings: list[str]
    line_start: int | None
    line_end: int | None

    @classmethod
    def from_citation(cls, citation: AnswerCitation) -> AnswerCitationResponse:
        """Build a public citation without leaking local storage details."""

        location = citation.chunk.location
        return cls(
            source_id=citation.source_id,
            score=citation.score,
            document=DocumentResponse.from_document(citation.document),
            chunk_id=citation.chunk.id,
            chunk_sequence=citation.chunk.sequence,
            excerpt=citation.chunk.text,
            page_start=location.page_start,
            page_end=location.page_end,
            headings=list(location.headings),
            line_start=location.line_start,
            line_end=location.line_end,
        )


class AnswerUsageResponse(BaseModel):
    """Provider token usage for one answer request."""

    input_tokens: int
    output_tokens: int


class AnswerResponse(BaseModel):
    """A grounded answer and its validated, traceable citations."""

    question: str
    answerable: bool
    answer: str
    citations: list[AnswerCitationResponse]
    model: str
    usage: AnswerUsageResponse

    @classmethod
    def from_answer(cls, answer: GroundedAnswer) -> AnswerResponse:
        """Build an HTTP response from a validated grounded answer."""

        return cls(
            question=answer.question,
            answerable=answer.answerable,
            answer=answer.answer,
            citations=[
                AnswerCitationResponse.from_citation(citation)
                for citation in answer.citations
            ],
            model=answer.model,
            usage=AnswerUsageResponse(
                input_tokens=answer.input_tokens,
                output_tokens=answer.output_tokens,
            ),
        )
