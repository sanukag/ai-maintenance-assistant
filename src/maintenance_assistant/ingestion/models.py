"""Domain values shared by document-ingestion stages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DocumentFormat(StrEnum):
    """Document formats supported by the initial ingestion pipeline."""

    PDF = "pdf"
    TEXT = "text"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class ValidatedDocument:
    """A local document that is safe to pass to an extractor."""

    path: Path
    filename: str
    format: DocumentFormat
    size_bytes: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """The human-readable location of text within its source document."""

    page_number: int | None = None
    heading: str | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    """A structural unit produced by a document extractor."""

    text: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Text and source structure extracted from a validated document."""

    source: ValidatedDocument
    title: str
    segments: tuple[ExtractedSegment, ...]
    page_count: int | None = None
