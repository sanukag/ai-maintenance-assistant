"""Text extraction for supported document formats."""

from __future__ import annotations

import re

from pypdf import PdfReader, __version__ as pypdf_version
from pypdf.errors import PdfReadError

from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode
from maintenance_assistant.ingestion.models import (
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    SourceLocation,
    ValidatedDocument,
)

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def extract_document(document: ValidatedDocument) -> ExtractedDocument:
    """Extract source-aware text using the document's validated format."""

    if document.format is DocumentFormat.PDF:
        return _extract_pdf(document)
    if document.format is DocumentFormat.MARKDOWN:
        return _extract_markdown(document)
    return _extract_text(document)


def _extract_text(document: ValidatedDocument) -> ExtractedDocument:
    text = _read_utf8(document)
    if not text.strip():
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "Document does not contain extractable text",
        )
    line_count = len(text.splitlines()) or 1
    segment = ExtractedSegment(
        text=text,
        location=SourceLocation(line_start=1, line_end=line_count),
    )
    return ExtractedDocument(
        source=document,
        title=document.path.stem,
        segments=(segment,),
    )


def _extract_markdown(document: ValidatedDocument) -> ExtractedDocument:
    text = _read_utf8(document)
    lines = text.splitlines()
    segments: list[ExtractedSegment] = []
    current_heading: str | None = None
    section_start = 1
    section_lines: list[str] = []
    title = document.path.stem

    def finish_section(line_end: int) -> None:
        content = "\n".join(section_lines).strip()
        if content:
            segments.append(
                ExtractedSegment(
                    text=content,
                    location=SourceLocation(
                        heading=current_heading,
                        line_start=section_start,
                        line_end=line_end,
                    ),
                )
            )

    for line_number, line in enumerate(lines, start=1):
        heading_match = _MARKDOWN_HEADING.match(line)
        if heading_match:
            finish_section(line_number - 1)
            current_heading = heading_match.group(1).strip()
            if title == document.path.stem:
                title = current_heading
            section_start = line_number
            section_lines = [line]
        else:
            section_lines.append(line)
    finish_section(len(lines))

    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "Document does not contain extractable text",
        )
    return ExtractedDocument(source=document, title=title, segments=tuple(segments))


def _extract_pdf(document: ValidatedDocument) -> ExtractedDocument:
    try:
        reader = PdfReader(document.path)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise IngestionError(
                IngestionErrorCode.ENCRYPTED_DOCUMENT,
                "Encrypted PDFs require a password and cannot be ingested",
            )
        segments = tuple(
            ExtractedSegment(
                text=text,
                location=SourceLocation(page_number=page_number),
            )
            for page_number, page in enumerate(reader.pages, start=1)
            if (text := (page.extract_text() or "").strip())
        )
    except IngestionError:
        raise
    except (OSError, PdfReadError, ValueError) as error:
        raise IngestionError(
            IngestionErrorCode.EXTRACTION_FAILED,
            f"PDF text could not be extracted from {document.filename}",
        ) from error

    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "PDF does not contain extractable text; it may be scanned",
        )
    metadata_title = getattr(reader.metadata, "title", None) if reader.metadata else None
    return ExtractedDocument(
        source=document,
        title=(metadata_title or document.path.stem).strip(),
        segments=segments,
        page_count=len(reader.pages),
        extractor_name="pypdf",
        extractor_version=pypdf_version,
    )


def _read_utf8(document: ValidatedDocument) -> str:
    try:
        return document.path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestionError(
            IngestionErrorCode.INVALID_DOCUMENT,
            f"Text document must use UTF-8 encoding: {document.filename}",
        ) from error
    except OSError as error:
        raise IngestionError(
            IngestionErrorCode.FILE_UNREADABLE,
            f"Document could not be read: {document.filename}",
        ) from error
