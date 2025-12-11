from pathlib import Path

import pytest

from maintenance_assistant.ingestion import (
    DocumentFormat,
    ExtractedDocument,
    NormalisedDocument,
    NormalisedSegment,
    SourceLocation,
    ValidatedDocument,
    chunk_document,
)
from maintenance_assistant.ingestion.chunking import TiktokenCounter


class CharacterCounter:
    """Make token-boundary expectations readable in focused unit tests."""

    @staticmethod
    def count(text: str) -> int:
        return len(text)


def _document(*segments: NormalisedSegment) -> NormalisedDocument:
    source = ValidatedDocument(
        path=Path("manual.md"),
        filename="manual.md",
        format=DocumentFormat.MARKDOWN,
        size_bytes=100,
        content_hash="hash",
    )
    extracted = ExtractedDocument(source=source, title="Manual", segments=())
    return NormalisedDocument(extracted=extracted, segments=segments)


def test_chunk_document_keeps_small_paragraphs_together() -> None:
    document = _document(
        NormalisedSegment(
            "First procedure.\n\nSecond procedure.",
            SourceLocation(page_number=2, heading="Isolation"),
        )
    )

    chunks = chunk_document(
        document,
        chunk_size_tokens=100,
        overlap_tokens=10,
        token_counter=CharacterCounter(),
    )

    assert [chunk.text for chunk in chunks] == [
        "First procedure.\n\nSecond procedure."
    ]
    assert chunks[0].sequence == 0
    assert chunks[0].location.page_start == 2
    assert chunks[0].location.headings == ("Isolation",)
    assert chunks[0].token_count == len(chunks[0].text)


def test_chunk_document_adds_bounded_overlap() -> None:
    document = _document(
        NormalisedSegment(
            "Alpha procedure has details.\n\nBeta procedure has details.",
            SourceLocation(line_start=1, line_end=4),
        )
    )

    chunks = chunk_document(
        document,
        chunk_size_tokens=38,
        overlap_tokens=10,
        token_counter=CharacterCounter(),
    )

    assert len(chunks) == 2
    assert chunks[0].text == "Alpha procedure has details."
    assert chunks[1].text.startswith("details.")
    assert chunks[1].text.endswith("Beta procedure has details.")
    assert all(chunk.character_count <= 38 for chunk in chunks)


def test_chunk_document_splits_oversized_paragraph_on_words() -> None:
    document = _document(
        NormalisedSegment("one two three four five six", SourceLocation(page_number=1))
    )

    chunks = chunk_document(
        document,
        chunk_size_tokens=13,
        overlap_tokens=0,
        token_counter=CharacterCounter(),
    )

    assert [chunk.text for chunk in chunks] == ["one two three", "four five six"]


def test_chunk_document_combines_source_ranges() -> None:
    document = _document(
        NormalisedSegment("Page two", SourceLocation(page_number=2, heading="Start")),
        NormalisedSegment("Page three", SourceLocation(page_number=3, heading="End")),
    )

    chunk = chunk_document(
        document,
        chunk_size_tokens=100,
        overlap_tokens=0,
        token_counter=CharacterCounter(),
    )[0]

    assert chunk.location.page_start == 2
    assert chunk.location.page_end == 3
    assert chunk.location.headings == ("Start", "End")


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (10, -1), (10, 10)],
)
def test_chunk_document_rejects_invalid_limits(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_document(
            _document(),
            chunk_size_tokens=chunk_size,
            overlap_tokens=overlap,
            token_counter=CharacterCounter(),
        )


def test_tiktoken_counter_counts_embedding_tokens_and_allows_special_text() -> None:
    counter = TiktokenCounter("cl100k_base")

    assert counter.count("pump isolation") == 3
    assert counter.count("Inspect <|endoftext|> literally") > 0


def test_tiktoken_counter_rejects_unknown_encoding() -> None:
    with pytest.raises(ValueError, match="Unknown token encoding"):
        TiktokenCounter("unknown-maintenance-encoding")


def test_chunk_document_splits_one_oversized_source_word() -> None:
    document = _document(
        NormalisedSegment("abcdefghij", SourceLocation(page_number=1))
    )

    chunks = chunk_document(
        document,
        chunk_size_tokens=4,
        overlap_tokens=0,
        token_counter=CharacterCounter(),
    )

    assert [chunk.text for chunk in chunks] == ["abcd", "efgh", "ij"]
    assert all(chunk.token_count <= 4 for chunk in chunks)
