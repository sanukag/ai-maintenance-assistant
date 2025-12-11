"""Local optical-character-recognition provider contracts and Tesseract runtime."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Protocol

from maintenance_assistant.config import Settings


class OCRError(Exception):
    """A safe failure raised by a local OCR provider."""


class OCRUnavailableError(OCRError):
    """Raised when the configured OCR executable cannot run."""


class OCRTimeoutError(OCRError):
    """Raised when OCR exceeds its per-image time budget."""


class OCRProvider(Protocol):
    """The local OCR behaviour required by document extraction."""

    name: str
    version: str | None
    available: bool

    def extract_image(
        self,
        path: Path,
        *,
        language: str,
        dpi: int,
        timeout_seconds: int,
    ) -> str:
        """Return recognised text from one image."""


class TesseractOCRProvider:
    """Recognise local document images with the Tesseract command-line engine."""

    name = "tesseract"

    def __init__(self, command: str = "tesseract") -> None:
        self.command = shutil.which(command)
        self.available = self.command is not None
        self.version = self._read_version() if self.command is not None else None

    def extract_image(
        self,
        path: Path,
        *,
        language: str,
        dpi: int,
        timeout_seconds: int,
    ) -> str:
        """Run bounded OCR and return UTF-8 text without exposing engine output."""

        if self.command is None:
            raise OCRUnavailableError("OCR requires the local Tesseract executable")
        try:
            completed = subprocess.run(
                [
                    self.command,
                    str(path),
                    "stdout",
                    "-l",
                    language,
                    "--dpi",
                    str(dpi),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise OCRTimeoutError(
                "OCR exceeded the configured time limit for one page"
            ) from error
        except OSError as error:
            raise OCRUnavailableError(
                "The local OCR engine could not be started"
            ) from error
        if completed.returncode != 0:
            raise OCRError(
                "The local OCR engine could not recognise this document image"
            )
        return completed.stdout.strip()

    def _read_version(self) -> str | None:
        try:
            completed = subprocess.run(
                [self.command, "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        return completed.stdout.splitlines()[0].removeprefix("tesseract ").strip()


def create_ocr_provider(settings: Settings) -> OCRProvider | None:
    """Create the configured local OCR provider or disable OCR explicitly."""

    if settings.ocr_provider == "none":
        return None
    if settings.ocr_provider == "tesseract":
        return TesseractOCRProvider()
    raise ValueError(f"Unsupported OCR provider: {settings.ocr_provider}")
