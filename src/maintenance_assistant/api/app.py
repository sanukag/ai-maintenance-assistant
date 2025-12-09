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
    IngestionError,
    IngestionErrorCode,
    LocalDocumentStore,
)

_CONFIGURED_EMBEDDING_PROVIDER = object()
_CONFIGURED_ANSWER_PROVIDER = object()


def create_app(
    *,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None | object = _CONFIGURED_EMBEDDING_PROVIDER,
    answer_provider: AnswerProvider | None | object = _CONFIGURED_ANSWER_PROVIDER,
    store: LocalDocumentStore | None = None,
) -> FastAPI:
    """Create an API with production defaults or explicitly injected services."""

    configured_settings = settings or Settings.from_environment()
    configured_embedding_provider = (
        create_embedding_provider(configured_settings)
        if embedding_provider is _CONFIGURED_EMBEDDING_PROVIDER
        else cast(EmbeddingProvider | None, embedding_provider)
    )
    configured_answer_provider = (
        create_answer_provider(configured_settings)
        if answer_provider is _CONFIGURED_ANSWER_PROVIDER
        else cast(AnswerProvider | None, answer_provider)
    )
    application = FastAPI(
        title="AI Maintenance Assistant API",
        version="0.1.0",
        description=(
            "Ingest maintenance documents, search their traceable chunks and "
            "generate grounded answers with verified citations."
        ),
    )
    application.state.services = build_services(
        configured_settings,
        configured_embedding_provider,
        configured_answer_provider,
        store,
    )
    application.include_router(router)

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
    if code is IngestionErrorCode.STORAGE_FAILED:
        return 500
    return 422


app = create_app()
