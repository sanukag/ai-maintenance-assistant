"""Semantic and hybrid retrieval over locally stored manual chunks."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
from typing import TYPE_CHECKING
import logging

from maintenance_assistant.ingestion.models import (
    DocumentMetadata,
    LexicalSearchResult,
    VectorSearchResult,
    metadata_embedding_text,
)
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.vector_index import QdrantVectorIndex, VectorIndexError
from maintenance_assistant.reranking import Reranker, RerankingError

if TYPE_CHECKING:
    from maintenance_assistant.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass
class _FusedCandidate:
    """Hold the source result and diagnostic scores while rankings are fused."""

    source: VectorSearchResult | LexicalSearchResult
    rrf_score: float = 0.0
    semantic_score: float | None = None
    lexical_score: float | None = None
    methods: list[str] = field(default_factory=list)


class VectorSearchService:
    """Embed a query and rank locally stored chunks by cosine similarity."""

    def __init__(
        self,
        store: LocalDocumentStore,
        embedding_provider: EmbeddingProvider,
        vector_index: QdrantVectorIndex | None = None,
    ) -> None:
        self.store = store
        self.embedding_provider = embedding_provider
        self.vector_index = vector_index

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        document_id: str | None = None,
        metadata: DocumentMetadata | None = None,
    ) -> tuple[VectorSearchResult, ...]:
        """Return the most semantically related local chunks."""

        if not query.strip():
            raise ValueError("search query must not be empty")
        selected_metadata = metadata or DocumentMetadata()
        batch = self.embedding_provider.embed(
            [metadata_embedding_text(query, selected_metadata)]
        )
        if len(batch.vectors) != 1:
            raise ValueError("embedding provider did not return one query vector")
        if self.vector_index is not None:
            try:
                scores = self.vector_index.search(
                    batch.vectors[0],
                    limit=limit,
                    document_id=document_id,
                    metadata=selected_metadata,
                )
                if scores:
                    return self.store.hydrate_vector_results(scores, model=batch.model)
            except VectorIndexError:
                logger.exception("Indexed vector search failed; using SQLite fallback")
        return self.store.search_vectors(batch.vectors[0], model=batch.model, limit=limit, document_id=document_id, metadata=selected_metadata)


class HybridSearchService:
    """Fuse dense-vector and SQLite full-text rankings with weighted RRF."""

    def __init__(
        self,
        store: LocalDocumentStore,
        embedding_provider: EmbeddingProvider,
        *,
        candidate_limit: int = 30,
        rrf_k: int = 60,
        semantic_weight: float = 1.0,
        text_weight: float = 1.0,
        vector_index: QdrantVectorIndex | None = None,
        reranker: Reranker | None = None,
        rerank_candidate_limit: int = 15,
        rerank_min_score: float = 0.25,
    ) -> None:
        if candidate_limit < 1:
            raise ValueError("candidate_limit must be greater than zero")
        if rrf_k < 1:
            raise ValueError("rrf_k must be greater than zero")
        if not isfinite(semantic_weight) or not isfinite(text_weight):
            raise ValueError("retrieval weights must be finite")
        if semantic_weight < 0 or text_weight < 0:
            raise ValueError("retrieval weights must not be negative")
        if semantic_weight == text_weight == 0:
            raise ValueError("at least one retrieval weight must be greater than zero")
        self.store = store
        self.embedding_provider = embedding_provider
        self.candidate_limit = candidate_limit
        self.rrf_k = rrf_k
        self.semantic_weight = semantic_weight
        self.text_weight = text_weight
        if rerank_candidate_limit < 1:
            raise ValueError("rerank_candidate_limit must be greater than zero")
        if not 0 <= rerank_min_score <= 1:
            raise ValueError("rerank_min_score must be between zero and one")
        self.reranker = reranker
        self.rerank_candidate_limit = rerank_candidate_limit
        self.rerank_min_score = rerank_min_score
        self.vector_search = VectorSearchService(store, embedding_provider, vector_index)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        document_id: str | None = None,
        metadata: DocumentMetadata | None = None,
    ) -> tuple[VectorSearchResult, ...]:
        """Return chunks ranked by fused semantic and exact-text evidence."""

        if not query.strip():
            raise ValueError("search query must not be empty")
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        candidate_limit = max(limit, self.candidate_limit)
        semantic = (
            self.vector_search.search(
                query,
                limit=candidate_limit,
                document_id=document_id,
                metadata=metadata,
            )
            if self.semantic_weight > 0
            else ()
        )
        lexical = (
            self.store.search_text(
                query,
                limit=candidate_limit,
                document_id=document_id,
                metadata=metadata,
            )
            if self.text_weight > 0
            else ()
        )

        candidates: dict[str, _FusedCandidate] = {}
        for rank, result in enumerate(semantic, start=1):
            candidates[result.chunk.id] = _FusedCandidate(
                source=result,
                rrf_score=self.semantic_weight / (self.rrf_k + rank),
                semantic_score=result.score,
                methods=["semantic"],
            )
        for rank, result in enumerate(lexical, start=1):
            state = candidates.setdefault(
                result.chunk.id,
                _FusedCandidate(source=result),
            )
            state.rrf_score += self.text_weight / (self.rrf_k + rank)
            state.lexical_score = result.score
            state.methods.append("text")

        maximum_rrf = (
            self.semantic_weight + self.text_weight
        ) / (self.rrf_k + 1)
        ranked = sorted(
            candidates.values(),
            key=lambda state: (
                -state.rrf_score,
                state.source.chunk.id,
            ),
        )
        results: list[VectorSearchResult] = []
        result_count = self.rerank_candidate_limit if self.reranker is not None else limit
        for state in ranked[:result_count]:
            source = state.source
            fusion_score = state.rrf_score / maximum_rrf
            results.append(
                VectorSearchResult(
                    score=fusion_score,
                    model=self.embedding_provider.model,
                    chunk=source.chunk,
                    document=source.document,
                    parent=source.parent,
                    semantic_score=state.semantic_score,
                    lexical_score=state.lexical_score,
                    retrieval_methods=tuple(state.methods),
                    fusion_score=fusion_score,
                )
            )
        if self.reranker is not None and results:
            try:
                scores = {
                    item.chunk_id: item.score
                    for item in self.reranker.rerank(query, results)
                }
                reranked = [
                    replace(result, score=scores[result.chunk.id], rerank_score=scores[result.chunk.id])
                    for result in results
                    if scores.get(result.chunk.id, -1) >= self.rerank_min_score
                ]
                reranked.sort(key=lambda result: (-result.score, result.chunk.id))
                return tuple(reranked[:limit])
            except RerankingError:
                logger.exception("Candidate reranking failed; using fused retrieval order")
        return tuple(results[:limit])
