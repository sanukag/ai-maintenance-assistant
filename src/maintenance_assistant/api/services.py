"""Application services shared by API routes."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

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


def build_services(
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    store: LocalDocumentStore | None = None,
) -> ApiServices:
    """Wire API-facing services to one store and provider configuration."""

    configured_store = store or LocalDocumentStore(settings.data_directory)
    return ApiServices(
        settings=settings,
        store=configured_store,
        ingestion=IngestionService(
            settings,
            store=configured_store,
            embedding_provider=embedding_provider,
        ),
        search=VectorSearchService(configured_store, embedding_provider)
        if embedding_provider is not None
        else None,
        embedding_provider=embedding_provider,
    )


def get_services(request: Request) -> ApiServices:
    """Return the services attached to the current application."""

    return request.app.state.services
