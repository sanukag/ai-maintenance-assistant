from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ocr import (
    OCRError,
    OCRTimeoutError,
    OCRUnavailableError,
    TesseractOCRProvider,
    create_ocr_provider,
)


def test_tesseract_provider_extracts_text_with_bounded_command(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"image")
    version = SimpleNamespace(returncode=0, stdout="tesseract 5.5.0\n")
    result = SimpleNamespace(returncode=0, stdout="Pump isolation\n")

    with (
        patch("maintenance_assistant.ocr.shutil.which", return_value="/bin/tesseract"),
        patch("maintenance_assistant.ocr.subprocess.run", side_effect=[version, result]) as run,
    ):
        provider = TesseractOCRProvider()
        text = provider.extract_image(
            image,
            language="eng",
            dpi=300,
            timeout_seconds=20,
        )

    assert provider.available is True
    assert provider.version == "5.5.0"
    assert text == "Pump isolation"
    assert run.call_args_list[1].args[0] == [
        "/bin/tesseract",
        str(image),
        "stdout",
        "-l",
        "eng",
        "--dpi",
        "300",
    ]


def test_tesseract_provider_maps_unavailable_timeout_and_engine_failure(
    tmp_path: Path,
) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"image")
    with patch("maintenance_assistant.ocr.shutil.which", return_value=None):
        unavailable = TesseractOCRProvider()
    with pytest.raises(OCRUnavailableError):
        unavailable.extract_image(
            image, language="eng", dpi=300, timeout_seconds=1
        )

    version = SimpleNamespace(returncode=0, stdout="tesseract 5\n")
    with (
        patch("maintenance_assistant.ocr.shutil.which", return_value="tesseract"),
        patch(
            "maintenance_assistant.ocr.subprocess.run",
            side_effect=[version, subprocess.TimeoutExpired("tesseract", 1)],
        ),
    ):
        timed = TesseractOCRProvider()
        with pytest.raises(OCRTimeoutError):
            timed.extract_image(image, language="eng", dpi=300, timeout_seconds=1)

    failed = SimpleNamespace(returncode=1, stdout="")
    with (
        patch("maintenance_assistant.ocr.shutil.which", return_value="tesseract"),
        patch("maintenance_assistant.ocr.subprocess.run", side_effect=[version, failed]),
    ):
        provider = TesseractOCRProvider()
        with pytest.raises(OCRError):
            provider.extract_image(image, language="eng", dpi=300, timeout_seconds=1)


def test_ocr_factory_can_disable_local_recognition() -> None:
    assert create_ocr_provider(Settings(ocr_provider="none")) is None
