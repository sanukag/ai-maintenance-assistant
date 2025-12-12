"""Public request and response models for the application API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from maintenance_assistant.answering import AnswerCitation, GroundedAnswer
from maintenance_assistant.conversations import (
    Conversation,
    ConversationCitation,
    ConversationDetail,
    ConversationMessage,
    ResponseFeedback,
)
from maintenance_assistant.ingestion import (
    DocumentLifecycleStatus,
    DocumentMetadata,
    IngestionResult,
    ReindexResult,
    StoredChunk,
    StoredDocument,
    StoredParentChunk,
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
    ocr: str
    ocr_engine: str | None
    ocr_version: str | None
    visual_analysis: str
    visual_analysis_model: str | None
    embeddings: str
    embedding_model: str | None
    answers: str
    answer_model: str | None


class DocumentMetadataResponse(BaseModel):
    """Worker-supplied equipment and document classification."""

    brand: list[str]
    machine: list[str]
    site: list[str]
    document_type: list[str]

    @classmethod
    def from_metadata(cls, metadata: DocumentMetadata) -> DocumentMetadataResponse:
        return cls(
            brand=metadata.brand,
            machine=metadata.machine,
            site=metadata.site,
            document_type=metadata.document_type,
        )


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
    metadata: DocumentMetadataResponse

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
            metadata=DocumentMetadataResponse.from_metadata(document.metadata),
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


class MetadataOptionsResponse(BaseModel):
    """Reusable metadata values available for tagging and retrieval filters."""

    brand: list[str]
    machine: list[str]
    site: list[str]
    document_type: list[str]

    @classmethod
    def from_metadata(cls, metadata: DocumentMetadata) -> MetadataOptionsResponse:
        return cls(
            brand=list(metadata.brand),
            machine=list(metadata.machine),
            site=list(metadata.site),
            document_type=list(metadata.document_type),
        )


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


class MetadataFilterRequest(BaseModel):
    """Optional exact document metadata criteria shared by search requests."""

    brand: list[str] = Field(default_factory=list, max_length=20)
    machine: list[str] = Field(default_factory=list, max_length=20)
    site: list[str] = Field(default_factory=list, max_length=20)
    document_type: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("brand", "machine", "site", "document_type", mode="before")
    @classmethod
    def accept_single_metadata_value(cls, value: object) -> object:
        """Retain compatibility with clients that send one scalar value."""

        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("brand", "machine", "site", "document_type")
    @classmethod
    def normalise_metadata(cls, values: list[str]) -> list[str]:
        """Apply the same metadata constraints used by ingestion."""

        return list(DocumentMetadata(brand=values).brand)

    def as_metadata(self) -> DocumentMetadata:
        return DocumentMetadata(
            brand=self.brand,
            machine=self.machine,
            site=self.site,
            document_type=self.document_type,
        )


class SearchRequest(MetadataFilterRequest):
    """A hybrid query and optional document filter."""

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
    """A stored text chunk returned by hybrid search."""

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


class ParentContextResponse(BaseModel):
    """A larger source section associated with a retrieved child chunk."""

    id: str
    sequence: int
    text: str
    character_count: int
    token_count: int
    location: ChunkLocationResponse

    @classmethod
    def from_parent(cls, parent: StoredParentChunk) -> ParentContextResponse:
        """Build a response from a stored parent context section."""

        return cls(
            id=parent.id,
            sequence=parent.sequence,
            text=parent.text,
            character_count=parent.character_count,
            token_count=parent.token_count,
            location=ChunkLocationResponse(
                page_start=parent.location.page_start,
                page_end=parent.location.page_end,
                headings=list(parent.location.headings),
                line_start=parent.location.line_start,
                line_end=parent.location.line_end,
            ),
        )


class SearchResultResponse(BaseModel):
    """One hybrid-ranked chunk and its document metadata."""

    score: float
    semantic_score: float | None
    lexical_score: float | None
    retrieval_methods: list[str]
    model: str
    document: DocumentResponse
    chunk: ChunkResponse
    parent_context: ParentContextResponse | None

    @classmethod
    def from_result(cls, result: VectorSearchResult) -> SearchResultResponse:
        """Build a response from a vector search result."""

        return cls(
            score=result.score,
            semantic_score=result.semantic_score,
            lexical_score=result.lexical_score,
            retrieval_methods=list(result.retrieval_methods),
            model=result.model,
            document=DocumentResponse.from_document(result.document),
            chunk=ChunkResponse.from_chunk(result.chunk),
            parent_context=(
                ParentContextResponse.from_parent(result.parent)
                if result.parent is not None
                else None
            ),
        )


class SearchResponse(BaseModel):
    """Ranked local semantic-search results."""

    results: list[SearchResultResponse]


class AnswerRequest(MetadataFilterRequest):
    """A question, evidence limit and optional document filter."""

    question: str = Field(min_length=1, max_length=2_000)
    max_sources: int = Field(default=5, ge=1, le=10)
    document_id: str | None = Field(default=None, min_length=1)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=100)

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
    parent_context_id: str | None
    excerpt: str
    page_start: int | None
    page_end: int | None
    headings: list[str]
    line_start: int | None
    line_end: int | None

    @classmethod
    def from_citation(cls, citation: AnswerCitation) -> AnswerCitationResponse:
        """Build a public citation without leaking local storage details."""

        evidence = citation.parent or citation.chunk
        location = evidence.location
        return cls(
            source_id=citation.source_id,
            score=citation.score,
            document=DocumentResponse.from_document(citation.document),
            chunk_id=citation.chunk.id,
            chunk_sequence=citation.chunk.sequence,
            parent_context_id=(citation.parent.id if citation.parent is not None else None),
            excerpt=evidence.text,
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

    conversation_id: str
    question: str
    answerable: bool
    answer: str
    citations: list[AnswerCitationResponse]
    model: str
    usage: AnswerUsageResponse

    @classmethod
    def from_answer(
        cls,
        answer: GroundedAnswer,
        conversation_id: str,
    ) -> AnswerResponse:
        """Build an HTTP response from a validated grounded answer."""

        return cls(
            conversation_id=conversation_id,
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


class ConversationSummaryResponse(BaseModel):
    """Conversation metadata for the history list."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int

    @classmethod
    def from_conversation(
        cls,
        conversation: Conversation,
    ) -> ConversationSummaryResponse:
        return cls(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=conversation.message_count,
        )


class ConversationListResponse(BaseModel):
    """A page of locally stored conversations."""

    items: list[ConversationSummaryResponse]
    limit: int
    offset: int


class ConversationCitationResponse(BaseModel):
    """A citation snapshot retained with an earlier assistant message."""

    source_id: str
    score: float
    document_id: str
    document_title: str
    original_filename: str
    chunk_id: str
    chunk_sequence: int
    parent_context_id: str | None
    excerpt: str
    page_start: int | None
    page_end: int | None
    headings: list[str]
    line_start: int | None
    line_end: int | None

    @classmethod
    def from_citation(
        cls,
        citation: ConversationCitation,
    ) -> ConversationCitationResponse:
        return cls(
            source_id=citation.source_id,
            score=citation.score,
            document_id=citation.document_id,
            document_title=citation.document_title,
            original_filename=citation.original_filename,
            chunk_id=citation.chunk_id,
            chunk_sequence=citation.chunk_sequence,
            parent_context_id=citation.parent_context_id,
            excerpt=citation.excerpt,
            page_start=citation.page_start,
            page_end=citation.page_end,
            headings=list(citation.headings),
            line_start=citation.line_start,
            line_end=citation.line_end,
        )


class ConversationMessageResponse(BaseModel):
    """One stored user or assistant message."""

    id: str
    sequence: int
    role: str
    content: str
    created_at: datetime
    scope_document_id: str | None
    answerable: bool | None
    model: str | None
    usage: AnswerUsageResponse | None
    citations: list[ConversationCitationResponse]
    feedback: ResponseFeedback | None
    scope_metadata: DocumentMetadataResponse

    @classmethod
    def from_message(
        cls,
        message: ConversationMessage,
    ) -> ConversationMessageResponse:
        usage = (
            AnswerUsageResponse(
                input_tokens=message.input_tokens or 0,
                output_tokens=message.output_tokens or 0,
            )
            if message.role.value == "assistant"
            else None
        )
        return cls(
            id=message.id,
            sequence=message.sequence,
            role=message.role.value,
            content=message.content,
            created_at=message.created_at,
            scope_document_id=message.scope_document_id,
            answerable=message.answerable,
            model=message.model,
            usage=usage,
            citations=[
                ConversationCitationResponse.from_citation(citation)
                for citation in message.citations
            ],
            feedback=message.feedback,
            scope_metadata=DocumentMetadataResponse.from_metadata(
                message.scope_metadata
            ),
        )


class ResponseFeedbackRequest(BaseModel):
    """A worker rating submitted for an assistant response."""

    rating: ResponseFeedback


class ResponseFeedbackResponse(BaseModel):
    """The stored current rating for an assistant response."""

    conversation_id: str
    message_id: str
    rating: ResponseFeedback


class ConversationResponse(BaseModel):
    """A complete conversation with every ordered message."""

    conversation: ConversationSummaryResponse
    messages: list[ConversationMessageResponse]

    @classmethod
    def from_detail(cls, detail: ConversationDetail) -> ConversationResponse:
        return cls(
            conversation=ConversationSummaryResponse.from_conversation(
                detail.conversation
            ),
            messages=[
                ConversationMessageResponse.from_message(message)
                for message in detail.messages
            ],
        )
