"""Application services shared by API routes."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from maintenance_assistant.answering import AnswerProvider, GroundedAnswerService
from maintenance_assistant.config import Settings
from maintenance_assistant.conversations import ConversationStore
from maintenance_assistant.embeddings import EmbeddingProvider
from maintenance_assistant.ingestion import IngestionService, LocalDocumentStore
from maintenance_assistant.jobs import IngestionJobStore
from maintenance_assistant.ocr import OCRProvider
from maintenance_assistant.retrieval import HybridSearchService
from maintenance_assistant.vision import VisualAnalysisProvider
from maintenance_assistant.vector_index import QdrantVectorIndex, create_vector_index


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Configured domain services owned by one API application."""

    settings: Settings
    store: LocalDocumentStore
    conversations: ConversationStore
    ingestion: IngestionService
    jobs: IngestionJobStore
    ocr_provider: OCRProvider | None
    visual_analysis_provider: VisualAnalysisProvider | None
    search: HybridSearchService | None
    embedding_provider: EmbeddingProvider | None
    answers: GroundedAnswerService | None
    answer_provider: AnswerProvider | None
    vector_index: QdrantVectorIndex | None


def build_services(
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    answer_provider: AnswerProvider | None,
    store: LocalDocumentStore | None = None,
    ocr_provider: OCRProvider | None = None,
    visual_analysis_provider: VisualAnalysisProvider | None = None,
) -> ApiServices:
    """Wire API-facing services to one store and provider configuration."""

    configured_store = store or LocalDocumentStore(settings.data_directory)
    vector_index = create_vector_index(settings, configured_store)
    search = (
        HybridSearchService(
            configured_store,
            embedding_provider,
            candidate_limit=settings.retrieval_candidate_limit,
            rrf_k=settings.retrieval_rrf_k,
            semantic_weight=settings.retrieval_semantic_weight,
            text_weight=settings.retrieval_text_weight,
            vector_index=vector_index,
        )
        if embedding_provider is not None
        else None
    )
    return ApiServices(
        settings=settings,
        store=configured_store,
        conversations=ConversationStore(configured_store),
        ingestion=IngestionService(
            settings,
            store=configured_store,
            embedding_provider=embedding_provider,
            ocr_provider=ocr_provider,
            visual_analysis_provider=visual_analysis_provider,
            vector_index=vector_index,
        ),
        jobs=IngestionJobStore(configured_store),
        ocr_provider=ocr_provider,
        visual_analysis_provider=visual_analysis_provider,
        search=search,
        embedding_provider=embedding_provider,
        answers=GroundedAnswerService(search, answer_provider)
        if search is not None and answer_provider is not None
        else None,
        answer_provider=answer_provider,
        vector_index=vector_index,
    )


def get_services(request: Request) -> ApiServices:
    """Return the services attached to the current application."""

    return request.app.state.services
