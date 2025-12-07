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

    chunks = chunk_document(document, chunk_size=100, overlap=10)

    assert [chunk.text for chunk in chunks] == [
        "First procedure.\n\nSecond procedure."
    ]
    assert chunks[0].sequence == 0
    assert chunks[0].location.page_start == 2
    assert chunks[0].location.headings == ("Isolation",)


def test_chunk_document_adds_bounded_overlap() -> None:
    document = _document(
        NormalisedSegment(
            "Alpha procedure has details.\n\nBeta procedure has details.",
            SourceLocation(line_start=1, line_end=4),
        )
    )

    chunks = chunk_document(document, chunk_size=38, overlap=10)

    assert len(chunks) == 2
    assert chunks[0].text == "Alpha procedure has details."
    assert chunks[1].text.startswith("details.")
    assert chunks[1].text.endswith("Beta procedure has details.")
    assert all(chunk.character_count <= 38 for chunk in chunks)


def test_chunk_document_splits_oversized_paragraph_on_words() -> None:
    document = _document(
        NormalisedSegment("one two three four five six", SourceLocation(page_number=1))
    )

    chunks = chunk_document(document, chunk_size=13, overlap=0)

    assert [chunk.text for chunk in chunks] == ["one two three", "four five six"]


def test_chunk_document_combines_source_ranges() -> None:
    document = _document(
        NormalisedSegment("Page two", SourceLocation(page_number=2, heading="Start")),
        NormalisedSegment("Page three", SourceLocation(page_number=3, heading="End")),
    )

    chunk = chunk_document(document, chunk_size=100, overlap=0)[0]

    assert chunk.location.page_start == 2
    assert chunk.location.page_end == 3
    assert chunk.location.headings == ("Start", "End")


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (10, -1), (10, 10)],
)
def test_chunk_document_rejects_invalid_limits(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_document(_document(), chunk_size=chunk_size, overlap=overlap)
