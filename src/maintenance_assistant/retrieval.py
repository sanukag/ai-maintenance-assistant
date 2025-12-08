"""Semantic retrieval over locally stored document vectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maintenance_assistant.ingestion.models import VectorSearchResult
from maintenance_assistant.ingestion.storage import LocalDocumentStore

if TYPE_CHECKING:
    from maintenance_assistant.embeddings import EmbeddingProvider


class VectorSearchService:
    """Embed a query and rank locally stored chunks by cosine similarity."""

    def __init__(
        self,
        store: LocalDocumentStore,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.store = store
        self.embedding_provider = embedding_provider

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        document_id: str | None = None,
    ) -> tuple[VectorSearchResult, ...]:
        """Return the most semantically related local chunks."""

        if not query.strip():
            raise ValueError("search query must not be empty")
        batch = self.embedding_provider.embed([query])
        if len(batch.vectors) != 1:
            raise ValueError("embedding provider did not return one query vector")
        return self.store.search_vectors(
            batch.vectors[0],
            model=batch.model,
            limit=limit,
            document_id=document_id,
        )
