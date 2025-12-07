"""Document-ingestion components."""

from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode
from maintenance_assistant.ingestion.extractors import extract_document
from maintenance_assistant.ingestion.models import (
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    SourceLocation,
    ValidatedDocument,
)
from maintenance_assistant.ingestion.validation import validate_document

__all__ = [
    "DocumentFormat",
    "ExtractedDocument",
    "ExtractedSegment",
    "IngestionError",
    "IngestionErrorCode",
    "SourceLocation",
    "ValidatedDocument",
    "extract_document",
    "validate_document",
]
