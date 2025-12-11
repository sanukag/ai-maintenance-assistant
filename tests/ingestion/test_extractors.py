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
from tests.fakes import FixedOCRProvider
from tests.ingestion.pdf_factory import write_scanned_image, write_scanned_pdf


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


def test_extract_pdf_requires_ocr_for_scanned_document(tmp_path: Path) -> None:
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

    assert captured.value.code is IngestionErrorCode.OCR_UNAVAILABLE


def test_extract_scanned_pdf_renders_page_for_local_ocr(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    write_scanned_pdf(path, "PUMP ISOLATION\nClose valve V1")
    provider = FixedOCRProvider("PUMP ISOLATION\nClose valve V1")

    extracted = extract_document(
        validate_document(path, Settings()),
        ocr_provider=provider,
        ocr_language="eng",
        ocr_dpi=200,
        ocr_page_timeout_seconds=12,
    )

    assert extracted.segments[0].text == "PUMP ISOLATION\nClose valve V1"
    assert extracted.segments[0].location.page_number == 1
    assert extracted.page_count == 1
    assert extracted.extractor_name == "pypdf+test-ocr"
    assert "pdfium" in extracted.extractor_version
    assert provider.calls[0][1:] == ("eng", 200, 12)


def test_extract_mixed_pdf_preserves_native_text_and_ocr_page(tmp_path: Path) -> None:
    path = tmp_path / "mixed.pdf"
    path.write_bytes(b"%PDF-test")
    reader = SimpleNamespace(
        is_encrypted=False,
        pages=[
            SimpleNamespace(extract_text=lambda: "Native pump overview"),
            SimpleNamespace(extract_text=lambda: ""),
        ],
        metadata=None,
    )
    provider = FixedOCRProvider("Scanned isolation procedure")

    with (
        patch("maintenance_assistant.ingestion.extractors.PdfReader", return_value=reader),
        patch(
            "maintenance_assistant.ingestion.extractors._ocr_pdf_pages",
            return_value={1: "Scanned isolation procedure"},
        ) as recognise,
    ):
        extracted = extract_document(
            validate_document(path, Settings()),
            ocr_provider=provider,
        )

    assert [segment.text for segment in extracted.segments] == [
        "Native pump overview",
        "Scanned isolation procedure",
    ]
    assert [segment.location.page_number for segment in extracted.segments] == [1, 2]
    recognise.assert_called_once()


def test_extract_pdf_enforces_ocr_page_and_pixel_limits(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    write_scanned_pdf(path, "PUMP ISOLATION")
    provider = FixedOCRProvider("PUMP ISOLATION")

    with pytest.raises(IngestionError) as pages:
        extract_document(
            validate_document(path, Settings()),
            ocr_provider=provider,
            ocr_max_pages=0,
        )
    assert pages.value.code is IngestionErrorCode.OCR_FAILED

    with pytest.raises(IngestionError) as pixels:
        extract_document(
            validate_document(path, Settings()),
            ocr_provider=provider,
            ocr_max_image_pixels=100,
        )
    assert pixels.value.code is IngestionErrorCode.OCR_FAILED


def test_extract_scanned_image_uses_local_ocr(tmp_path: Path) -> None:
    path = tmp_path / "procedure.png"
    write_scanned_image(path, "CHECK MOTOR ROTATION")
    provider = FixedOCRProvider("CHECK MOTOR ROTATION")

    extracted = extract_document(
        validate_document(path, Settings()),
        ocr_provider=provider,
    )

    assert extracted.source.format.value == "image"
    assert extracted.segments[0].text == "CHECK MOTOR ROTATION"
    assert extracted.page_count == 1
    assert extracted.extractor_name == "test-ocr"


def test_extract_text_rejects_non_utf8_content(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_bytes(b"\xff\xfe\x80")

    with pytest.raises(IngestionError) as captured:
        extract_document(validate_document(path, Settings()))

    assert captured.value.code is IngestionErrorCode.INVALID_DOCUMENT
