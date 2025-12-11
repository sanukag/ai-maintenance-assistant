"""Structure-aware, token-bounded document chunking with source traceability."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import as_file, files
import re
from typing import Protocol

import tiktoken
from tiktoken.load import load_tiktoken_bpe

from maintenance_assistant.ingestion.models import (
    ChunkLocation,
    NormalisedDocument,
    PreparedChunk,
    SourceLocation,
)

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")
_CL100K_HASH = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
_CL100K_PATTERN = (
    r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"
    r" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"
)


class TokenCounter(Protocol):
    """Count model tokens without coupling chunking logic to one tokenizer."""

    def count(self, text: str) -> int: ...


class TiktokenCounter:
    """Count text using a named OpenAI-compatible tiktoken encoding."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = encoding_name
        try:
            self._encoding = _encoding(encoding_name)
        except ValueError as error:
            raise ValueError(f"Unknown token encoding: {encoding_name}") from error

    def count(self, text: str) -> int:
        """Return the exact encoded token count for text."""

        return len(self._encoding.encode(text, disallowed_special=()))


@lru_cache(maxsize=8)
def _encoding(name: str) -> tiktoken.Encoding:
    if name != "cl100k_base":
        raise ValueError(name)
    resource = files("maintenance_assistant").joinpath(
        "assets/cl100k_base.tiktoken"
    )
    with as_file(resource) as encoding_path:
        mergeable_ranks = load_tiktoken_bpe(
            str(encoding_path),
            expected_hash=_CL100K_HASH,
        )
    return tiktoken.Encoding(
        name="cl100k_base",
        pat_str=_CL100K_PATTERN,
        mergeable_ranks=mergeable_ranks,
        special_tokens={
            "<|endoftext|>": 100257,
            "<|fim_prefix|>": 100258,
            "<|fim_middle|>": 100259,
            "<|fim_suffix|>": 100260,
            "<|endofprompt|>": 100276,
        },
    )


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    location: SourceLocation


def chunk_document(
    document: NormalisedDocument,
    *,
    chunk_size_tokens: int = 300,
    overlap_tokens: int = 40,
    token_counter: TokenCounter | None = None,
) -> tuple[PreparedChunk, ...]:
    """Split a document into token-bounded chunks while retaining source ranges."""

    if chunk_size_tokens < 1:
        raise ValueError("chunk_size_tokens must be greater than zero")
    if overlap_tokens < 0 or overlap_tokens >= chunk_size_tokens:
        raise ValueError(
            "overlap_tokens must be zero or greater and smaller than "
            "chunk_size_tokens"
        )
    counter = token_counter or TiktokenCounter()
    units = tuple(_iter_units(document, chunk_size_tokens, counter))
    chunks: list[PreparedChunk] = []
    current: list[_Unit] = []

    for unit in units:
        proposed = _join_units((*current, unit))
        if current and counter.count(proposed) > chunk_size_tokens:
            chunks.append(_prepare_chunk(len(chunks), current, counter))
            current = _overlap_units(current, overlap_tokens, counter)
            while (
                current
                and counter.count(_join_units((*current, unit))) > chunk_size_tokens
            ):
                current.pop(0)
        current.append(unit)

    if current:
        chunks.append(_prepare_chunk(len(chunks), current, counter))
    return tuple(chunks)


def _iter_units(
    document: NormalisedDocument,
    chunk_size_tokens: int,
    counter: TokenCounter,
):
    for segment in document.segments:
        paragraphs = _PARAGRAPH_BREAK.split(segment.text)
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            for part in _split_long_text(paragraph, chunk_size_tokens, counter):
                yield _Unit(text=part, location=segment.location)


def _split_long_text(
    text: str,
    maximum_tokens: int,
    counter: TokenCounter,
) -> tuple[str, ...]:
    if counter.count(text) <= maximum_tokens:
        return (text,)

    words = text.split()
    if not words:
        return ()
    parts: list[str] = []
    current: list[str] = []
    for word in words:
        if counter.count(word) > maximum_tokens:
            if current:
                parts.append(" ".join(current))
                current = []
            parts.extend(_split_oversized_word(word, maximum_tokens, counter))
            continue
        proposed = " ".join((*current, word))
        if current and counter.count(proposed) > maximum_tokens:
            parts.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        parts.append(" ".join(current))
    return tuple(parts)


def _split_oversized_word(
    word: str,
    maximum_tokens: int,
    counter: TokenCounter,
) -> tuple[str, ...]:
    parts: list[str] = []
    remaining = word
    while remaining:
        end = _largest_prefix(remaining, maximum_tokens, counter)
        if end == 0:
            raise ValueError(
                "chunk_size_tokens is too small for one source character"
            )
        parts.append(remaining[:end])
        remaining = remaining[end:]
    return tuple(parts)


def _overlap_units(
    units: list[_Unit],
    overlap_tokens: int,
    counter: TokenCounter,
) -> list[_Unit]:
    if overlap_tokens == 0:
        return []
    selected: list[_Unit] = []
    for unit in reversed(units):
        ordered = [unit, *reversed(selected)]
        if counter.count(_join_units(ordered)) <= overlap_tokens:
            selected.append(unit)
            continue
        tail = _word_aligned_tail(unit.text, overlap_tokens, counter)
        if tail:
            selected.append(_Unit(text=tail, location=unit.location))
        break
    return list(reversed(selected))


def _word_aligned_tail(
    text: str,
    maximum_tokens: int,
    counter: TokenCounter,
) -> str:
    start = _smallest_suffix(text, maximum_tokens, counter)
    tail = text[start:].lstrip()
    if start > 0 and tail and not text[start - 1].isspace():
        _, separator, remainder = tail.partition(" ")
        if separator:
            tail = remainder
    return tail


def _largest_prefix(text: str, maximum_tokens: int, counter: TokenCounter) -> int:
    low = 1
    high = len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if counter.count(text[:middle]) <= maximum_tokens:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _smallest_suffix(text: str, maximum_tokens: int, counter: TokenCounter) -> int:
    low = 0
    high = len(text)
    best = len(text)
    while low <= high:
        middle = (low + high) // 2
        if counter.count(text[middle:]) <= maximum_tokens:
            best = middle
            high = middle - 1
        else:
            low = middle + 1
    return best


def _prepare_chunk(
    sequence: int,
    units: list[_Unit],
    counter: TokenCounter,
) -> PreparedChunk:
    text = _join_units(units)
    locations = [unit.location for unit in units]
    pages = [
        location.page_number
        for location in locations
        if location.page_number is not None
    ]
    headings = tuple(
        dict.fromkeys(
            location.heading for location in locations if location.heading is not None
        )
    )
    line_starts = [
        location.line_start
        for location in locations
        if location.line_start is not None
    ]
    line_ends = [
        location.line_end for location in locations if location.line_end is not None
    ]
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
        token_count=counter.count(text),
    )


def _join_units(units) -> str:
    return "\n\n".join(unit.text for unit in units)
