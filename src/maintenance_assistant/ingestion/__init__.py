"""Document-ingestion components."""

from maintenance_assistant.ingestion.errors import (
    DocumentLifecycleError,
    DocumentLifecycleErrorCode,
    DuplicateDocumentError,
    IngestionError,
    IngestionErrorCode,
)
from maintenance_assistant.ingestion.chunking import (
    chunk_document,
    chunk_document_hierarchy,
)
from maintenance_assistant.ingestion.extractors import extract_document
from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    DocumentLifecycleStatus,
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    IngestionResult,
    IngestionStatus,
    NormalisedDocument,
    NormalisedSegment,
    PreparedChunk,
    PreparedChunkHierarchy,
    PreparedEmbedding,
    PreparedParentChunk,
    ReindexResult,
    SourceLocation,
    StoredChunk,
    StoredDocument,
    StoredEmbedding,
    StoredParentChunk,
    ValidatedDocument,
    VectorSearchResult,
)
from maintenance_assistant.ingestion.normalisation import normalise_document
from maintenance_assistant.ingestion.service import IngestionService
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.ingestion.validation import validate_document

__all__ = [
    "ChunkLocation",
    "DocumentLifecycleStatus",
    "DocumentFormat",
    "DocumentLifecycleError",
    "DocumentLifecycleErrorCode",
    "DuplicateDocumentError",
    "ExtractedDocument",
    "ExtractedSegment",
    "IngestionError",
    "IngestionErrorCode",
    "IngestionResult",
    "IngestionService",
    "IngestionStatus",
    "NormalisedDocument",
    "NormalisedSegment",
    "PreparedChunk",
    "PreparedChunkHierarchy",
    "PreparedEmbedding",
    "PreparedParentChunk",
    "ReindexResult",
    "SourceLocation",
    "StoredChunk",
    "StoredDocument",
    "StoredEmbedding",
    "StoredParentChunk",
    "ValidatedDocument",
    "VectorSearchResult",
    "chunk_document",
    "chunk_document_hierarchy",
    "extract_document",
    "normalise_document",
    "LocalDocumentStore",
    "validate_document",
]
