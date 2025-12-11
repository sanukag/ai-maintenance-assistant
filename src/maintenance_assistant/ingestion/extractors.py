"""Text extraction for supported document formats."""

from __future__ import annotations

from importlib.metadata import version as package_version
from pathlib import Path
import re
import tempfile

import pypdfium2 as pdfium

from pypdf import PdfReader, __version__ as pypdf_version
from pypdf.errors import PdfReadError

from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode
from maintenance_assistant.ingestion.models import (
    DocumentFormat,
    ExtractedDocument,
    ExtractedSegment,
    SourceLocation,
    ValidatedDocument,
)
from maintenance_assistant.ocr import (
    OCRError,
    OCRProvider,
    OCRTimeoutError,
    OCRUnavailableError,
)

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def extract_document(
    document: ValidatedDocument,
    *,
    ocr_provider: OCRProvider | None = None,
    ocr_language: str = "eng",
    ocr_dpi: int = 300,
    ocr_page_timeout_seconds: int = 30,
    ocr_max_pages: int = 100,
    ocr_max_image_pixels: int = 50_000_000,
) -> ExtractedDocument:
    """Extract source-aware text using the document's validated format."""

    if document.format is DocumentFormat.PDF:
        return _extract_pdf(
            document,
            ocr_provider=ocr_provider,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            ocr_page_timeout_seconds=ocr_page_timeout_seconds,
            ocr_max_pages=ocr_max_pages,
            ocr_max_image_pixels=ocr_max_image_pixels,
        )
    if document.format is DocumentFormat.IMAGE:
        return _extract_image(
            document,
            ocr_provider=ocr_provider,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            ocr_page_timeout_seconds=ocr_page_timeout_seconds,
        )
    if document.format is DocumentFormat.MARKDOWN:
        return _extract_markdown(document)
    return _extract_text(document)


def _extract_text(document: ValidatedDocument) -> ExtractedDocument:
    text = _read_utf8(document)
    if not text.strip():
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "Document does not contain extractable text",
        )
    line_count = len(text.splitlines()) or 1
    segment = ExtractedSegment(
        text=text,
        location=SourceLocation(line_start=1, line_end=line_count),
    )
    return ExtractedDocument(
        source=document,
        title=document.path.stem,
        segments=(segment,),
    )


def _extract_markdown(document: ValidatedDocument) -> ExtractedDocument:
    text = _read_utf8(document)
    lines = text.splitlines()
    segments: list[ExtractedSegment] = []
    current_heading: str | None = None
    section_start = 1
    section_lines: list[str] = []
    title = document.path.stem

    def finish_section(line_end: int) -> None:
        content = "\n".join(section_lines).strip()
        if content:
            segments.append(
                ExtractedSegment(
                    text=content,
                    location=SourceLocation(
                        heading=current_heading,
                        line_start=section_start,
                        line_end=line_end,
                    ),
                )
            )

    for line_number, line in enumerate(lines, start=1):
        heading_match = _MARKDOWN_HEADING.match(line)
        if heading_match:
            finish_section(line_number - 1)
            current_heading = heading_match.group(1).strip()
            if title == document.path.stem:
                title = current_heading
            section_start = line_number
            section_lines = [line]
        else:
            section_lines.append(line)
    finish_section(len(lines))

    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "Document does not contain extractable text",
        )
    return ExtractedDocument(source=document, title=title, segments=tuple(segments))


def _extract_pdf(
    document: ValidatedDocument,
    *,
    ocr_provider: OCRProvider | None,
    ocr_language: str,
    ocr_dpi: int,
    ocr_page_timeout_seconds: int,
    ocr_max_pages: int,
    ocr_max_image_pixels: int,
) -> ExtractedDocument:
    try:
        reader = PdfReader(document.path)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise IngestionError(
                IngestionErrorCode.ENCRYPTED_DOCUMENT,
                "Encrypted PDFs require a password and cannot be ingested",
            )
        page_text = [(page.extract_text() or "").strip() for page in reader.pages]
    except IngestionError:
        raise
    except (OSError, PdfReadError, ValueError) as error:
        raise IngestionError(
            IngestionErrorCode.EXTRACTION_FAILED,
            f"PDF text could not be extracted from {document.filename}",
        ) from error

    ocr_page_indexes = [index for index, text in enumerate(page_text) if not text]
    used_ocr = False
    if ocr_provider is not None and ocr_page_indexes:
        if not ocr_provider.available:
            raise IngestionError(
                IngestionErrorCode.OCR_UNAVAILABLE,
                "PDF has no text layer and the local OCR engine is unavailable",
            )
        if len(ocr_page_indexes) > ocr_max_pages:
            raise IngestionError(
                IngestionErrorCode.OCR_FAILED,
                f"Document requires OCR on more than {ocr_max_pages} pages",
            )
        recognised = _ocr_pdf_pages(
            document.path,
            ocr_page_indexes,
            ocr_provider=ocr_provider,
            language=ocr_language,
            dpi=ocr_dpi,
            timeout_seconds=ocr_page_timeout_seconds,
            maximum_pixels=ocr_max_image_pixels,
        )
        for page_index, text in recognised.items():
            page_text[page_index] = text
            used_ocr = used_ocr or bool(text)

    segments = tuple(
        ExtractedSegment(
            text=text,
            location=SourceLocation(page_number=page_number),
        )
        for page_number, text in enumerate(page_text, start=1)
        if text
    )
    if not segments and ocr_provider is None:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            "PDF has no text layer and OCR is disabled",
        )
    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "PDF does not contain text that could be extracted or recognised",
        )
    metadata_title = getattr(reader.metadata, "title", None) if reader.metadata else None
    extractor_name = "pypdf"
    extractor_version = pypdf_version
    if used_ocr and ocr_provider is not None:
        extractor_name = f"pypdf+{ocr_provider.name}"
        extractor_version = (
            f"pypdf {pypdf_version}; {ocr_provider.name} "
            f"{ocr_provider.version or 'unknown'}; pdfium {package_version('pypdfium2')}"
        )
    return ExtractedDocument(
        source=document,
        title=(metadata_title or document.path.stem).strip(),
        segments=segments,
        page_count=len(reader.pages),
        extractor_name=extractor_name,
        extractor_version=extractor_version,
    )


def _extract_image(
    document: ValidatedDocument,
    *,
    ocr_provider: OCRProvider | None,
    ocr_language: str,
    ocr_dpi: int,
    ocr_page_timeout_seconds: int,
) -> ExtractedDocument:
    if ocr_provider is None:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            "Image documents require an enabled OCR provider",
        )
    if not ocr_provider.available:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            "Image documents require an available local OCR engine",
        )
    try:
        text = ocr_provider.extract_image(
            document.path,
            language=ocr_language,
            dpi=ocr_dpi,
            timeout_seconds=ocr_page_timeout_seconds,
        ).strip()
    except OCRUnavailableError as error:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            str(error),
        ) from error
    except OCRTimeoutError as error:
        raise IngestionError(IngestionErrorCode.OCR_TIMED_OUT, str(error)) from error
    except OCRError as error:
        raise IngestionError(IngestionErrorCode.OCR_FAILED, str(error)) from error
    if not text:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "OCR did not recognise text in the document image",
        )
    return ExtractedDocument(
        source=document,
        title=document.path.stem,
        segments=(
            ExtractedSegment(text=text, location=SourceLocation(page_number=1)),
        ),
        page_count=1,
        extractor_name=ocr_provider.name,
        extractor_version=ocr_provider.version or "unknown",
    )


def _ocr_pdf_pages(
    path: Path,
    page_indexes: list[int],
    *,
    ocr_provider: OCRProvider,
    language: str,
    dpi: int,
    timeout_seconds: int,
    maximum_pixels: int,
) -> dict[int, str]:
    recognised: dict[int, str] = {}
    try:
        pdf = pdfium.PdfDocument(str(path))
        with tempfile.TemporaryDirectory(prefix="ama-ocr-") as temporary_directory:
            for page_index in page_indexes:
                page = pdf[page_index]
                try:
                    width = round(page.get_width() * dpi / 72)
                    height = round(page.get_height() * dpi / 72)
                    if width * height > maximum_pixels:
                        raise IngestionError(
                            IngestionErrorCode.OCR_FAILED,
                            "A scanned PDF page exceeds the configured pixel limit",
                        )
                    bitmap = page.render(scale=dpi / 72, grayscale=True)
                    try:
                        image_path = (
                            Path(temporary_directory) / f"page-{page_index + 1}.png"
                        )
                        image = bitmap.to_pil()
                        try:
                            image.save(image_path, format="PNG", dpi=(dpi, dpi))
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                recognised[page_index] = ocr_provider.extract_image(
                    image_path,
                    language=language,
                    dpi=dpi,
                    timeout_seconds=timeout_seconds,
                ).strip()
    except IngestionError:
        raise
    except OCRUnavailableError as error:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            str(error),
        ) from error
    except OCRTimeoutError as error:
        raise IngestionError(IngestionErrorCode.OCR_TIMED_OUT, str(error)) from error
    except OCRError as error:
        raise IngestionError(IngestionErrorCode.OCR_FAILED, str(error)) from error
    except (OSError, ValueError, pdfium.PdfiumError) as error:
        raise IngestionError(
            IngestionErrorCode.EXTRACTION_FAILED,
            "Scanned PDF pages could not be prepared for OCR",
        ) from error
    finally:
        if "pdf" in locals():
            pdf.close()
    return recognised


def _read_utf8(document: ValidatedDocument) -> str:
    try:
        return document.path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestionError(
            IngestionErrorCode.INVALID_DOCUMENT,
            f"Text document must use UTF-8 encoding: {document.filename}",
        ) from error
    except OSError as error:
        raise IngestionError(
            IngestionErrorCode.FILE_UNREADABLE,
            f"Document could not be read: {document.filename}",
        ) from error
