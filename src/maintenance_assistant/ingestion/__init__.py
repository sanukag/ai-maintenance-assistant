"""Document-ingestion components."""

from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode
from maintenance_assistant.ingestion.chunking import chunk_document
from maintenance_assistant.ingestion.extractors import extract_document
from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    NormalisedDocument,
    NormalisedSegment,
    PreparedChunk,
    SourceLocation,
    ValidatedDocument,
)
from maintenance_assistant.ingestion.normalisation import normalise_document
from maintenance_assistant.ingestion.validation import validate_document

__all__ = [
    "ChunkLocation",
    "DocumentFormat",
    "ExtractedDocument",
    "ExtractedSegment",
    "IngestionError",
    "IngestionErrorCode",
    "NormalisedDocument",
    "NormalisedSegment",
    "PreparedChunk",
    "SourceLocation",
    "ValidatedDocument",
    "chunk_document",
    "extract_document",
    "normalise_document",
    "validate_document",
]
