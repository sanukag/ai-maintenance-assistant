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
    DUPLICATE_DOCUMENT = "duplicate_document"
    STORAGE_FAILED = "storage_failed"


class IngestionError(Exception):
    """A safe, categorised ingestion failure."""

    def __init__(self, code: IngestionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
