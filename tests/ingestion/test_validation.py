from hashlib import sha256
from pathlib import Path

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import (
    DocumentFormat,
    IngestionError,
    IngestionErrorCode,
    validate_document,
)
from tests.ingestion.pdf_factory import write_scanned_image


def test_validate_document_fingerprints_supported_file(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    content = b"Check the pump seal before starting."
    path.write_bytes(content)

    document = validate_document(path, Settings())

    assert document.path == path.resolve()
    assert document.filename == "pump.txt"
    assert document.format is DocumentFormat.TEXT
    assert document.size_bytes == len(content)
    assert document.content_hash == sha256(content).hexdigest()


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("missing.txt", None, IngestionErrorCode.FILE_NOT_FOUND),
        ("empty.txt", b"", IngestionErrorCode.EMPTY_FILE),
        ("manual.docx", b"content", IngestionErrorCode.UNSUPPORTED_TYPE),
        ("manual.pdf", b"not a PDF", IngestionErrorCode.INVALID_DOCUMENT),
        ("manual.txt", b"text\x00binary", IngestionErrorCode.INVALID_DOCUMENT),
    ],
)
def test_validate_document_rejects_invalid_input(
    tmp_path: Path,
    filename: str,
    content: bytes | None,
    code: IngestionErrorCode,
) -> None:
    path = tmp_path / filename
    if content is not None:
        path.write_bytes(content)

    with pytest.raises(IngestionError) as captured:
        validate_document(path, Settings())

    assert captured.value.code is code


def test_validate_document_enforces_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_bytes(b"x" * 1_048_577)

    with pytest.raises(IngestionError) as captured:
        validate_document(path, Settings(max_document_size_mb=1))

    assert captured.value.code is IngestionErrorCode.FILE_TOO_LARGE


@pytest.mark.parametrize(("suffix", "image_format"), [(".png", "PNG"), (".jpg", "JPEG")])
def test_validate_document_accepts_scanned_images(
    tmp_path: Path,
    suffix: str,
    image_format: str,
) -> None:
    path = tmp_path / f"scan{suffix}"
    write_scanned_image(path, "Pump procedure", image_format=image_format)

    document = validate_document(path, Settings())

    assert document.format is DocumentFormat.IMAGE


def test_validate_document_rejects_disguised_or_oversized_images(
    tmp_path: Path,
) -> None:
    disguised = tmp_path / "manual.png"
    disguised.write_bytes(b"not an image")
    with pytest.raises(IngestionError) as invalid:
        validate_document(disguised, Settings())
    assert invalid.value.code is IngestionErrorCode.INVALID_DOCUMENT

    large = tmp_path / "large.png"
    write_scanned_image(large, "Pump procedure")
    with pytest.raises(IngestionError) as oversized:
        validate_document(large, Settings(ocr_max_image_pixels=100))
    assert oversized.value.code is IngestionErrorCode.FILE_TOO_LARGE
