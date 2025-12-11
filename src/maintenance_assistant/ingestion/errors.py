"""Structured failures raised by the ingestion pipeline."""

from __future__ import annotations

from enum import StrEnum


class IngestionErrorCode(StrEnum):
    """Stable error codes suitable for interfaces and logs."""

    FILE_NOT_FOUND = "file_not_found"
    FILE_UNREADABLE = "file_unreadable"
    UNSUPPORTED_TYPE = "unsupported_type"
    FILE_TOO_LARGE = "file_too_large"
    EMPTY_FILE = "empty_file"
    INVALID_DOCUMENT = "invalid_document"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    NO_EXTRACTABLE_TEXT = "no_extractable_text"
    EXTRACTION_FAILED = "extraction_failed"
    OCR_UNAVAILABLE = "ocr_unavailable"
    OCR_TIMED_OUT = "ocr_timed_out"
    OCR_FAILED = "ocr_failed"
    DUPLICATE_DOCUMENT = "duplicate_document"
    EMBEDDING_FAILED = "embedding_failed"
    STORAGE_FAILED = "storage_failed"


class IngestionError(Exception):
    """A safe, categorised ingestion failure."""

    def __init__(self, code: IngestionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DuplicateDocumentError(IngestionError):
    """Raised when an identical document is already stored."""

    def __init__(self, document_id: str) -> None:
        super().__init__(
            IngestionErrorCode.DUPLICATE_DOCUMENT,
            f"An identical document is already stored as {document_id}",
        )
        self.document_id = document_id


class DocumentLifecycleErrorCode(StrEnum):
    """Stable failures for manual revision and lifecycle operations."""

    DOCUMENT_NOT_FOUND = "document_not_found"
    REVISION_CONFLICT = "revision_conflict"
    IDENTICAL_REVISION = "identical_revision"


class DocumentLifecycleError(Exception):
    """A safe failure raised when a lifecycle transition cannot be completed."""

    def __init__(self, code: DocumentLifecycleErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
