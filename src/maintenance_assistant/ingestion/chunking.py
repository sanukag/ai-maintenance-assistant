"""Structure-aware document chunking with source traceability."""

from __future__ import annotations

from dataclasses import dataclass
import re

from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    NormalisedDocument,
    PreparedChunk,
    SourceLocation,
)

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    location: SourceLocation


def chunk_document(
    document: NormalisedDocument,
    *,
    chunk_size: int = 2400,
    overlap: int = 400,
) -> tuple[PreparedChunk, ...]:
    """Split a document into bounded chunks while retaining source ranges."""

    if chunk_size < 1:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be zero or greater and smaller than chunk_size")

    units = tuple(_iter_units(document, chunk_size))
    chunks: list[PreparedChunk] = []
    current: list[_Unit] = []

    for unit in units:
        proposed = _join_units((*current, unit))
        if current and len(proposed) > chunk_size:
            chunks.append(_prepare_chunk(len(chunks), current))
            current = _overlap_units(current, overlap)
            while current and len(_join_units((*current, unit))) > chunk_size:
                current.pop(0)
        current.append(unit)

    if current:
        chunks.append(_prepare_chunk(len(chunks), current))
    return tuple(chunks)


def _iter_units(document: NormalisedDocument, chunk_size: int):
    for segment in document.segments:
        paragraphs = _PARAGRAPH_BREAK.split(segment.text)
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            for part in _split_long_text(paragraph, chunk_size):
                yield _Unit(text=part, location=segment.location)


def _split_long_text(text: str, maximum: int) -> tuple[str, ...]:
    if len(text) <= maximum:
        return (text,)

    words = text.split()
    if not words:
        return ()
    parts: list[str] = []
    current: list[str] = []
    for word in words:
        if len(word) > maximum:
            if current:
                parts.append(" ".join(current))
                current = []
            parts.extend(word[index : index + maximum] for index in range(0, len(word), maximum))
            continue
        proposed = " ".join((*current, word))
        if current and len(proposed) > maximum:
            parts.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        parts.append(" ".join(current))
    return tuple(parts)


def _overlap_units(units: list[_Unit], overlap: int) -> list[_Unit]:
    if overlap == 0:
        return []
    selected: list[_Unit] = []
    used = 0
    for unit in reversed(units):
        separator = 2 if selected else 0
        remaining = overlap - used - separator
        if remaining <= 0:
            break
        if len(unit.text) <= remaining:
            selected.append(unit)
            used += separator + len(unit.text)
        else:
            tail = _word_aligned_tail(unit.text, remaining)
            if tail:
                selected.append(_Unit(text=tail, location=unit.location))
            break
    return list(reversed(selected))


def _word_aligned_tail(text: str, maximum: int) -> str:
    tail = text[-maximum:].lstrip()
    if len(text) > maximum and tail and not text[-maximum - 1].isspace():
        _, separator, remainder = tail.partition(" ")
        if separator:
            tail = remainder
    return tail


def _prepare_chunk(sequence: int, units: list[_Unit]) -> PreparedChunk:
    text = _join_units(units)
    locations = [unit.location for unit in units]
    pages = [location.page_number for location in locations if location.page_number is not None]
    headings = tuple(
        dict.fromkeys(
            location.heading for location in locations if location.heading is not None
        )
    )
    line_starts = [location.line_start for location in locations if location.line_start is not None]
    line_ends = [location.line_end for location in locations if location.line_end is not None]
    return PreparedChunk(
        sequence=sequence,
        text=text,
        character_count=len(text),
        location=ChunkLocation(
            page_start=min(pages, default=None),
            page_end=max(pages, default=None),
            headings=headings,
            line_start=min(line_starts, default=None),
            line_end=max(line_ends, default=None),
        ),
    )


def _join_units(units) -> str:
    return "\n\n".join(unit.text for unit in units)
