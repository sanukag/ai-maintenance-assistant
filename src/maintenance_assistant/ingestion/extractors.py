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
from maintenance_assistant.vision import (
    VisualAnalysis,
    VisualAnalysisError,
    VisualAnalysisProvider,
    VisualAnalysisTimeoutError,
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
    visual_analysis_provider: VisualAnalysisProvider | None = None,
    visual_analysis_render_dpi: int = 150,
    visual_analysis_max_pages: int = 100,
    visual_analysis_max_image_pixels: int = 25_000_000,
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
            visual_analysis_provider=visual_analysis_provider,
            visual_analysis_render_dpi=visual_analysis_render_dpi,
            visual_analysis_max_pages=visual_analysis_max_pages,
            visual_analysis_max_image_pixels=visual_analysis_max_image_pixels,
        )
    if document.format is DocumentFormat.IMAGE:
        return _extract_image(
            document,
            ocr_provider=ocr_provider,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            ocr_page_timeout_seconds=ocr_page_timeout_seconds,
            visual_analysis_provider=visual_analysis_provider,
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
    visual_analysis_provider: VisualAnalysisProvider | None,
    visual_analysis_render_dpi: int,
    visual_analysis_max_pages: int,
    visual_analysis_max_image_pixels: int,
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

    visual_analyses: dict[int, VisualAnalysis] = {}
    if visual_analysis_provider is not None:
        if not visual_analysis_provider.available:
            raise IngestionError(
                IngestionErrorCode.VISUAL_ANALYSIS_UNAVAILABLE,
                "The configured visual-analysis provider is unavailable",
            )
        if len(page_text) > visual_analysis_max_pages:
            raise IngestionError(
                IngestionErrorCode.VISUAL_ANALYSIS_FAILED,
                "Document exceeds the configured visual-analysis page limit",
            )
        visual_analyses = _analyse_pdf_pages(
            document.path,
            list(range(len(page_text))),
            provider=visual_analysis_provider,
            dpi=visual_analysis_render_dpi,
            maximum_pixels=visual_analysis_max_image_pixels,
        )

    segments_list: list[ExtractedSegment] = []
    for page_index, text in enumerate(page_text):
        page_number = page_index + 1
        if text:
            segments_list.append(
                ExtractedSegment(
                    text=text,
                    location=SourceLocation(page_number=page_number),
                )
            )
        analysis = visual_analyses.get(page_index)
        if analysis is not None:
            segments_list.append(_visual_segment(analysis, page_number))
    segments = tuple(segments_list)
    if not segments and ocr_provider is None and visual_analysis_provider is None:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            "PDF has no text layer and OCR and visual analysis are disabled",
        )
    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "PDF does not contain text that could be extracted or recognised",
        )
    metadata_title = getattr(reader.metadata, "title", None) if reader.metadata else None
    extractor_name = "pypdf"
    extractor_version = pypdf_version
    extractor_parts = ["pypdf"]
    extractor_versions = [f"pypdf {pypdf_version}"]
    if used_ocr and ocr_provider is not None:
        extractor_parts.append(ocr_provider.name)
        extractor_versions.append(
            f"{ocr_provider.name} {ocr_provider.version or 'unknown'}"
        )
    if visual_analysis_provider is not None:
        extractor_parts.append(visual_analysis_provider.name)
        extractor_versions.append(
            f"{visual_analysis_provider.name} {visual_analysis_provider.model}"
        )
    if len(extractor_parts) > 1:
        extractor_name = "+".join(extractor_parts)
        extractor_versions.append(f"pdfium {package_version('pypdfium2')}")
        extractor_version = "; ".join(extractor_versions)
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
    visual_analysis_provider: VisualAnalysisProvider | None,
) -> ExtractedDocument:
    if ocr_provider is None and visual_analysis_provider is None:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            "Image documents require OCR or visual analysis to be enabled",
        )
    if ocr_provider is not None and not ocr_provider.available:
        raise IngestionError(
            IngestionErrorCode.OCR_UNAVAILABLE,
            "Image documents require an available local OCR engine",
        )
    if visual_analysis_provider is not None and not visual_analysis_provider.available:
        raise IngestionError(
            IngestionErrorCode.VISUAL_ANALYSIS_UNAVAILABLE,
            "The configured visual-analysis provider is unavailable",
        )

    text = ""
    if ocr_provider is not None:
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

    analysis = (
        _analyse_image(document.path, visual_analysis_provider)
        if visual_analysis_provider is not None
        else None
    )
    segments: list[ExtractedSegment] = []
    if text:
        segments.append(
            ExtractedSegment(text=text, location=SourceLocation(page_number=1))
        )
    if analysis is not None:
        segments.append(_visual_segment(analysis, 1))
    if not segments:
        raise IngestionError(
            IngestionErrorCode.NO_EXTRACTABLE_TEXT,
            "The document image contained no recognised text or maintenance visual",
        )
    extractor_parts: list[str] = []
    extractor_versions: list[str] = []
    if ocr_provider is not None and text:
        extractor_parts.append(ocr_provider.name)
        extractor_versions.append(ocr_provider.version or "unknown")
    if visual_analysis_provider is not None:
        extractor_parts.append(visual_analysis_provider.name)
        extractor_versions.append(visual_analysis_provider.model)
    return ExtractedDocument(
        source=document,
        title=document.path.stem,
        segments=tuple(segments),
        page_count=1,
        extractor_name="+".join(extractor_parts),
        extractor_version="; ".join(extractor_versions),
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


def _analyse_pdf_pages(
    path: Path,
    page_indexes: list[int],
    *,
    provider: VisualAnalysisProvider,
    dpi: int,
    maximum_pixels: int,
) -> dict[int, VisualAnalysis]:
    analyses: dict[int, VisualAnalysis] = {}
    try:
        pdf = pdfium.PdfDocument(str(path))
        with tempfile.TemporaryDirectory(prefix="ama-vision-") as temporary_directory:
            for page_index in page_indexes:
                page = pdf[page_index]
                try:
                    width = round(page.get_width() * dpi / 72)
                    height = round(page.get_height() * dpi / 72)
                    if width * height > maximum_pixels:
                        raise IngestionError(
                            IngestionErrorCode.VISUAL_ANALYSIS_FAILED,
                            "A PDF page exceeds the configured visual-analysis pixel limit",
                        )
                    bitmap = page.render(scale=dpi / 72)
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
                analysis = _analyse_image(image_path, provider)
                if analysis is not None:
                    analyses[page_index] = analysis
    except IngestionError:
        raise
    except (OSError, ValueError, pdfium.PdfiumError) as error:
        raise IngestionError(
            IngestionErrorCode.EXTRACTION_FAILED,
            "PDF pages could not be prepared for visual analysis",
        ) from error
    finally:
        if "pdf" in locals():
            pdf.close()
    return analyses


def _analyse_image(
    path: Path,
    provider: VisualAnalysisProvider,
) -> VisualAnalysis | None:
    try:
        return provider.analyse_image(path)
    except VisualAnalysisTimeoutError as error:
        raise IngestionError(
            IngestionErrorCode.VISUAL_ANALYSIS_TIMED_OUT,
            str(error),
        ) from error
    except VisualAnalysisError as error:
        raise IngestionError(
            IngestionErrorCode.VISUAL_ANALYSIS_FAILED,
            str(error),
        ) from error


def _visual_segment(analysis: VisualAnalysis, page_number: int) -> ExtractedSegment:
    return ExtractedSegment(
        text=analysis.as_text(),
        location=SourceLocation(
            page_number=page_number,
            heading=f"Visual analysis: {analysis.visual_type.value}",
        ),
    )


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
