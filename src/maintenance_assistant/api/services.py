"""Application services shared by API routes."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from maintenance_assistant.answering import AnswerProvider, GroundedAnswerService
from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import EmbeddingProvider
from maintenance_assistant.ingestion import IngestionService, LocalDocumentStore
from maintenance_assistant.retrieval import VectorSearchService


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Configured domain services owned by one API application."""

    settings: Settings
    store: LocalDocumentStore
    ingestion: IngestionService
    search: VectorSearchService | None
    embedding_provider: EmbeddingProvider | None
    answers: GroundedAnswerService | None
    answer_provider: AnswerProvider | None


def build_services(
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    answer_provider: AnswerProvider | None,
    store: LocalDocumentStore | None = None,
) -> ApiServices:
    """Wire API-facing services to one store and provider configuration."""

    configured_store = store or LocalDocumentStore(settings.data_directory)
    search = (
        VectorSearchService(configured_store, embedding_provider)
        if embedding_provider is not None
        else None
    )
    return ApiServices(
        settings=settings,
        store=configured_store,
        ingestion=IngestionService(
            settings,
            store=configured_store,
            embedding_provider=embedding_provider,
        ),
        search=search,
        embedding_provider=embedding_provider,
        answers=GroundedAnswerService(search, answer_provider)
        if search is not None and answer_provider is not None
        else None,
        answer_provider=answer_provider,
    )


def get_services(request: Request) -> ApiServices:
    """Return the services attached to the current application."""

    return request.app.state.services
