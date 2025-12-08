"""Document-ingestion components."""

from maintenance_assistant.ingestion.errors import (
    DuplicateDocumentError,
    IngestionError,
    IngestionErrorCode,
)
from maintenance_assistant.ingestion.chunking import chunk_document
from maintenance_assistant.ingestion.extractors import extract_document
from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    IngestionResult,
    IngestionStatus,
    NormalisedDocument,
    NormalisedSegment,
    PreparedChunk,
    PreparedEmbedding,
    SourceLocation,
    StoredChunk,
    StoredDocument,
    StoredEmbedding,
    ValidatedDocument,
    VectorSearchResult,
)
from maintenance_assistant.ingestion.normalisation import normalise_document
from maintenance_assistant.ingestion.service import IngestionService
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.ingestion.validation import validate_document

__all__ = [
    "ChunkLocation",
    "DocumentFormat",
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
    "PreparedEmbedding",
    "SourceLocation",
    "StoredChunk",
    "StoredDocument",
    "StoredEmbedding",
    "ValidatedDocument",
    "VectorSearchResult",
    "chunk_document",
    "extract_document",
    "normalise_document",
    "LocalDocumentStore",
    "validate_document",
]
