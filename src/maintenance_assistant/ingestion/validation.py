"""Validation for untrusted local document inputs."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode
from maintenance_assistant.ingestion.models import DocumentFormat, ValidatedDocument

_FORMAT_BY_SUFFIX = {
    ".pdf": DocumentFormat.PDF,
    ".txt": DocumentFormat.TEXT,
    ".md": DocumentFormat.MARKDOWN,
}
_READ_SIZE = 64 * 1024


def validate_document(path: Path, settings: Settings) -> ValidatedDocument:
    """Validate and fingerprint a document without modifying it."""

    candidate = path.expanduser()
    if not candidate.exists():
        raise IngestionError(
            IngestionErrorCode.FILE_NOT_FOUND,
            f"Document does not exist: {candidate}",
        )
    if not candidate.is_file():
        raise IngestionError(
            IngestionErrorCode.FILE_UNREADABLE,
            f"Document is not a regular file: {candidate}",
        )

    suffix = candidate.suffix.lower()
    if suffix not in settings.supported_file_types or suffix not in _FORMAT_BY_SUFFIX:
        raise IngestionError(
            IngestionErrorCode.UNSUPPORTED_TYPE,
            f"Document type is not supported: {suffix or 'unknown'}",
        )

    try:
        size_bytes = candidate.stat().st_size
    except OSError as error:
        raise IngestionError(
            IngestionErrorCode.FILE_UNREADABLE,
            f"Document metadata could not be read: {candidate.name}",
        ) from error

    if size_bytes == 0:
        raise IngestionError(IngestionErrorCode.EMPTY_FILE, "Document is empty")
    maximum_bytes = settings.max_document_size_mb * 1024 * 1024
    if size_bytes > maximum_bytes:
        raise IngestionError(
            IngestionErrorCode.FILE_TOO_LARGE,
            f"Document exceeds the {settings.max_document_size_mb} MB limit",
        )

    content_hash, first_bytes = _fingerprint(candidate)
    document_format = _FORMAT_BY_SUFFIX[suffix]
    if document_format is DocumentFormat.PDF and not first_bytes.startswith(b"%PDF-"):
        raise IngestionError(
            IngestionErrorCode.INVALID_DOCUMENT,
            "File has a PDF extension but does not contain a PDF document",
        )
    if document_format is not DocumentFormat.PDF and b"\x00" in first_bytes:
        raise IngestionError(
            IngestionErrorCode.INVALID_DOCUMENT,
            "Text document contains binary data",
        )

    return ValidatedDocument(
        path=candidate.resolve(),
        filename=candidate.name,
        format=document_format,
        size_bytes=size_bytes,
        content_hash=content_hash,
    )


def _fingerprint(path: Path) -> tuple[str, bytes]:
    digest = sha256()
    first_bytes = b""
    try:
        with path.open("rb") as document:
            while block := document.read(_READ_SIZE):
                if not first_bytes:
                    first_bytes = block
                digest.update(block)
    except OSError as error:
        raise IngestionError(
            IngestionErrorCode.FILE_UNREADABLE,
            f"Document could not be read: {path.name}",
        ) from error
    return digest.hexdigest(), first_bytes
