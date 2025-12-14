"""FastAPI application construction and error translation."""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from maintenance_assistant.answering import (
    AnswerProvider,
    AnsweringError,
    create_answer_provider,
)
from maintenance_assistant.api.errors import ApiError
from maintenance_assistant.api.routes import router
from maintenance_assistant.api.schemas import ErrorDetail, ErrorResponse
from maintenance_assistant.api.services import build_services
from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import EmbeddingProvider, create_embedding_provider
from maintenance_assistant.ingestion import (
    DocumentLifecycleError,
    DocumentLifecycleErrorCode,
    IngestionError,
    IngestionErrorCode,
    LocalDocumentStore,
)
from maintenance_assistant.ocr import OCRProvider, create_ocr_provider
from maintenance_assistant.metrics import RuntimeMetrics
from maintenance_assistant.vision import (
    VisualAnalysisProvider,
    create_visual_analysis_provider,
)

_CONFIGURED_EMBEDDING_PROVIDER = object()
_CONFIGURED_ANSWER_PROVIDER = object()
_CONFIGURED_OCR_PROVIDER = object()
_CONFIGURED_VISUAL_ANALYSIS_PROVIDER = object()


def create_app(
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None | object = _CONFIGURED_EMBEDDING_PROVIDER,
    answer_provider: AnswerProvider | None | object = _CONFIGURED_ANSWER_PROVIDER,
    ocr_provider: OCRProvider | None | object = _CONFIGURED_OCR_PROVIDER,
    visual_analysis_provider: VisualAnalysisProvider | None | object = (
        _CONFIGURED_VISUAL_ANALYSIS_PROVIDER
    ),
    store: LocalDocumentStore | None = None,
) -> FastAPI:
    """Create an API with production defaults or explicitly injected services."""

    configured_settings = settings or Settings.from_environment()
    configured_store = store or LocalDocumentStore(
        configured_settings.data_directory,
        configured_settings.sqlite_busy_timeout_ms,
    )
    configured_embedding_provider = (
        create_embedding_provider(configured_settings, configured_store)
        if embedding_provider is _CONFIGURED_EMBEDDING_PROVIDER
        else cast(EmbeddingProvider | None, embedding_provider)
    )
    configured_answer_provider = (
        create_answer_provider(configured_settings)
        if answer_provider is _CONFIGURED_ANSWER_PROVIDER
        else cast(AnswerProvider | None, answer_provider)
    )
    configured_ocr_provider = (
        create_ocr_provider(configured_settings)
        if ocr_provider is _CONFIGURED_OCR_PROVIDER
        else cast(OCRProvider | None, ocr_provider)
    )
    configured_visual_analysis_provider = (
        create_visual_analysis_provider(configured_settings)
        if visual_analysis_provider is _CONFIGURED_VISUAL_ANALYSIS_PROVIDER
        else cast(VisualAnalysisProvider | None, visual_analysis_provider)
    )
    application = FastAPI(
        title="AI Maintenance Assistant API",
        version="0.1.0",
        description=(
            "Ingest maintenance documents, search their traceable chunks and "
            "generate grounded answers with verified citations."
        ),
    )
    application.state.runtime_metrics = RuntimeMetrics()
    application.state.services = build_services(
        settings=configured_settings,
        embedding_provider=configured_embedding_provider,
        answer_provider=configured_answer_provider,
        store=configured_store,
        ocr_provider=configured_ocr_provider,
        visual_analysis_provider=configured_visual_analysis_provider,
    )
    application.include_router(router)

    @application.middleware("http")
    async def measure_requests(request: Request, call_next):
        metrics: RuntimeMetrics = request.app.state.runtime_metrics
        started = metrics.start_request()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            metrics.finish_request(
                request.method,
                route_path,
                status_code,
                started,
            )

    @application.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        return _error_response(error.status_code, error.code, error.message)

    @application.exception_handler(IngestionError)
    async def handle_ingestion_error(_: Request, error: IngestionError) -> JSONResponse:
        return _error_response(
            _ingestion_status(error.code),
            error.code.value,
            error.message,
        )

    @application.exception_handler(DocumentLifecycleError)
    async def handle_lifecycle_error(
        _: Request, error: DocumentLifecycleError
    ) -> JSONResponse:
        status_code = (
            404
            if error.code is DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND
            else 409
        )
        return _error_response(status_code, error.code.value, error.message)

    @application.exception_handler(AnsweringError)
    async def handle_answering_error(_: Request, error: AnsweringError) -> JSONResponse:
        return _error_response(502, error.code.value, error.message)

    return application


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _ingestion_status(code: IngestionErrorCode) -> int:
    if code is IngestionErrorCode.FILE_NOT_FOUND:
        return 404
    if code is IngestionErrorCode.UNSUPPORTED_TYPE:
        return 415
    if code is IngestionErrorCode.FILE_TOO_LARGE:
        return 413
    if code is IngestionErrorCode.EMBEDDING_FAILED:
        return 502
    if code is IngestionErrorCode.OCR_UNAVAILABLE:
        return 503
    if code is IngestionErrorCode.OCR_TIMED_OUT:
        return 504
    if code is IngestionErrorCode.VISUAL_ANALYSIS_UNAVAILABLE:
        return 503
    if code is IngestionErrorCode.VISUAL_ANALYSIS_TIMED_OUT:
        return 504
    if code is IngestionErrorCode.VISUAL_ANALYSIS_FAILED:
        return 502
    if code is IngestionErrorCode.STORAGE_FAILED:
        return 500
    return 422


app = create_app()
