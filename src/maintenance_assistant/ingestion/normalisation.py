"""Conservative text normalisation for extracted documents."""

from __future__ import annotations

import re
import unicodedata

from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode
from maintenance_assistant.ingestion.models import (
    ExtractedDocument,
    NormalisedDocument,
    NormalisedSegment,
)

_EXCESSIVE_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def normalise_document(document: ExtractedDocument) -> NormalisedDocument:
    """Clean formatting noise without rewriting source meaning."""

    segments = tuple(
        NormalisedSegment(text=text, location=segment.location)
        for segment in document.segments
        if (text := normalise_text(segment.text))
    )
    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "Document does not contain usable text after normalisation",
        )
    return NormalisedDocument(extracted=document, segments=segments)


def normalise_text(text: str) -> str:
    """Apply deterministic whitespace and Unicode normalisation."""

    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = "".join(
        character
        for character in text
        if character in {"\n", "\t"} or not unicodedata.category(character).startswith("C")
    )
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _EXCESSIVE_BLANK_LINES.sub("\n\n", text)
    return text.strip()
