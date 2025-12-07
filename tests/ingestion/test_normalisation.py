from pathlib import Path

import pytest

from maintenance_assistant.ingestion import (
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    IngestionError,
    IngestionErrorCode,
    SourceLocation,
    ValidatedDocument,
    normalise_document,
)
from maintenance_assistant.ingestion.normalisation import normalise_text


def _extracted(*segments: ExtractedSegment) -> ExtractedDocument:
    source = ValidatedDocument(
        path=Path("manual.txt"),
        filename="manual.txt",
        format=DocumentFormat.TEXT,
        size_bytes=10,
        content_hash="hash",
    )
    return ExtractedDocument(source=source, title="Manual", segments=segments)


def test_normalise_text_cleans_formatting_without_rewriting_words() -> None:
    source = "  Cafe\u0301\u00a0pump  \r\n\r\n\r\nCheck\x00 pressure.  "

    assert normalise_text(source) == "Café pump\n\nCheck pressure."


def test_normalise_document_preserves_source_location() -> None:
    location = SourceLocation(page_number=3)

    document = normalise_document(_extracted(ExtractedSegment(" Pump\r\n", location)))

    assert document.segments[0].text == "Pump"
    assert document.segments[0].location is location


def test_normalise_document_rejects_content_made_only_of_controls() -> None:
    with pytest.raises(IngestionError) as captured:
        normalise_document(_extracted(ExtractedSegment("\x00\x01", SourceLocation())))

    assert captured.value.code is IngestionErrorCode.NO_EXTRACTABLE_TEXT
