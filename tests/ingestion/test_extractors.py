from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import (
    IngestionError,
    IngestionErrorCode,
    extract_document,
    validate_document,
)


def test_extract_plain_text_with_line_location(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Pump isolation\nCheck pressure\n", encoding="utf-8")

    extracted = extract_document(validate_document(path, Settings()))

    assert extracted.title == "pump"
    assert extracted.segments[0].text == "Pump isolation\nCheck pressure\n"
    assert extracted.segments[0].location.line_start == 1
    assert extracted.segments[0].location.line_end == 2


def test_extract_markdown_as_heading_sections(tmp_path: Path) -> None:
    path = tmp_path / "manual.md"
    path.write_text(
        "# Pump manual\n\nOverview.\n\n## Isolation\n\nClose valve V1.\n",
        encoding="utf-8",
    )

    extracted = extract_document(validate_document(path, Settings()))

    assert extracted.title == "Pump manual"
    assert [segment.location.heading for segment in extracted.segments] == [
        "Pump manual",
        "Isolation",
    ]
    assert "Close valve V1." in extracted.segments[1].text


def test_extract_pdf_with_page_locations(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(b"%PDF-test")
    reader = SimpleNamespace(
        is_encrypted=False,
        pages=[
            SimpleNamespace(extract_text=lambda: "Pump overview"),
            SimpleNamespace(extract_text=lambda: "Isolation procedure"),
        ],
        metadata=SimpleNamespace(title="Pump manual"),
    )

    with patch(
        "maintenance_assistant.ingestion.extractors.PdfReader", return_value=reader
    ):
        extracted = extract_document(validate_document(path, Settings()))

    assert extracted.title == "Pump manual"
    assert extracted.page_count == 2
    assert extracted.segments[1].location.page_number == 2


def test_extract_pdf_counts_pages_without_text(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(b"%PDF-test")
    reader = SimpleNamespace(
        is_encrypted=False,
        pages=[
            SimpleNamespace(extract_text=lambda: "Pump overview"),
            SimpleNamespace(extract_text=lambda: ""),
        ],
        metadata=None,
    )

    with patch(
        "maintenance_assistant.ingestion.extractors.PdfReader", return_value=reader
    ):
        extracted = extract_document(validate_document(path, Settings()))

    assert extracted.page_count == 2
    assert len(extracted.segments) == 1


def test_extract_pdf_rejects_scanned_document(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-test")
    reader = SimpleNamespace(
        is_encrypted=False,
        pages=[SimpleNamespace(extract_text=lambda: "")],
        metadata=None,
    )

    with (
        patch("maintenance_assistant.ingestion.extractors.PdfReader", return_value=reader),
        pytest.raises(IngestionError) as captured,
    ):
        extract_document(validate_document(path, Settings()))

    assert captured.value.code is IngestionErrorCode.NO_EXTRACTABLE_TEXT


def test_extract_text_rejects_non_utf8_content(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_bytes(b"\xff\xfe\x80")

    with pytest.raises(IngestionError) as captured:
        extract_document(validate_document(path, Settings()))

    assert captured.value.code is IngestionErrorCode.INVALID_DOCUMENT
