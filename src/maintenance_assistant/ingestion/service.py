"""End-to-end orchestration for local document ingestion."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion.chunking import (
    TiktokenCounter,
    chunk_document_hierarchy,
)
from maintenance_assistant.ingestion.errors import (
    DocumentLifecycleError,
    DocumentLifecycleErrorCode,
    DuplicateDocumentError,
    IngestionError,
    IngestionErrorCode,
)
from maintenance_assistant.ingestion.extractors import extract_document
from maintenance_assistant.ingestion.models import (
    IngestionResult,
    IngestionStatus,
    NormalisedDocument,
    PreparedChunk,
    PreparedChunkHierarchy,
    PreparedEmbedding,
    ReindexResult,
    StoredChunk,
    StoredDocument,
    ValidatedDocument,
)
from maintenance_assistant.ingestion.normalisation import normalise_document
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.ingestion.validation import validate_document
from maintenance_assistant.ocr import OCRProvider, create_ocr_provider

if TYPE_CHECKING:
    from maintenance_assistant.embeddings import EmbeddingProvider

_CONFIGURED_OCR_PROVIDER = object()


class IngestionService:
    """Coordinate validation, extraction, chunking and local persistence."""

    def __init__(
        self,
        settings: Settings,
        store: LocalDocumentStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        ocr_provider: OCRProvider | None | object = _CONFIGURED_OCR_PROVIDER,
    ) -> None:
        self.settings = settings
        self.store = store or LocalDocumentStore(settings.data_directory)
        self.embedding_provider = embedding_provider
        self.ocr_provider = (
            create_ocr_provider(settings)
            if ocr_provider is _CONFIGURED_OCR_PROVIDER
            else cast(OCRProvider | None, ocr_provider)
        )
        self.token_counter = TiktokenCounter(settings.chunk_token_encoding)

    def ingest(self, path: Path) -> IngestionResult:
        """Ingest one local document or return its existing stored record."""

        validated = validate_document(path, self.settings)
        existing = self.store.find_by_hash(validated.content_hash)
        if existing is not None:
            return self._result_for_existing(existing)

        extracted = self._extract(validated)
        normalised = normalise_document(extracted)
        hierarchy = self._prepare_hierarchy(normalised)
        embeddings, input_tokens = self._embed_prepared_chunks(hierarchy.children)
        try:
            stored = self.store.save(
                extracted,
                hierarchy.children,
                embeddings,
                parents=hierarchy.parents,
            )
        except DuplicateDocumentError as duplicate:
            stored = self.store.get_document(duplicate.document_id)
            if stored is None:
                raise
            return self._result_for_existing(stored)
        return IngestionResult(
            status=IngestionStatus.COMPLETED,
            document=stored,
            embedded_chunk_count=len(embeddings),
            embedding_model=self.embedding_provider.model
            if self.embedding_provider
            else None,
            embedding_input_tokens=input_tokens,
        )

    def ingest_revision(self, path: Path, document_id: str) -> IngestionResult:
        """Ingest a new revision and supersede one current stored manual."""

        previous = self.store.get_document(document_id)
        if previous is None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                "Document was not found",
            )
        if previous.lifecycle_status.value != "current":
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.REVISION_CONFLICT,
                "Only a current manual can be replaced",
            )

        validated = validate_document(path, self.settings)
        existing = self.store.find_by_hash(validated.content_hash)
        if existing is not None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.IDENTICAL_REVISION,
                "The replacement is identical to a manual already in the library",
            )
        extracted = self._extract(validated)
        normalised = normalise_document(extracted)
        hierarchy = self._prepare_hierarchy(normalised)
        embeddings, input_tokens = self._embed_prepared_chunks(hierarchy.children)
        stored = self.store.save(
            extracted,
            hierarchy.children,
            embeddings,
            parents=hierarchy.parents,
            supersedes_document_id=document_id,
        )
        return IngestionResult(
            status=IngestionStatus.COMPLETED,
            document=stored,
            embedded_chunk_count=len(embeddings),
            embedding_model=self.embedding_provider.model
            if self.embedding_provider
            else None,
            embedding_input_tokens=input_tokens,
        )

    def reindex(self, document_id: str) -> ReindexResult:
        """Regenerate every vector for a stored manual using the active provider."""

        document = self.store.get_document(document_id)
        if document is None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                "Document was not found",
            )
        if self.embedding_provider is None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.REVISION_CONFLICT,
                "Re-indexing requires an enabled embedding provider",
            )
        validated = validate_document(document.stored_path, self.settings)
        extracted = self._extract(validated)
        normalised = normalise_document(extracted)
        hierarchy = self._prepare_hierarchy(normalised)
        embeddings, input_tokens = self._embed_prepared_chunks(hierarchy.children)
        document = self.store.replace_chunks(
            document_id,
            extracted,
            hierarchy.parents,
            hierarchy.children,
            embeddings,
        )
        return ReindexResult(
            document=document,
            embedded_chunk_count=len(embeddings),
            embedding_model=self.embedding_provider.model,
            embedding_input_tokens=input_tokens,
        )

    def _result_for_existing(self, document: StoredDocument) -> IngestionResult:
        input_tokens = 0
        if self.embedding_provider is not None:
            missing = self.store.missing_embedding_chunks(
                document.id,
                model=self.embedding_provider.model,
                dimensions=self.embedding_provider.dimensions,
            )
            if missing:
                embeddings, input_tokens = self._embed_stored_chunks(missing)
                self.store.save_embeddings(document.id, embeddings)
            embedded_count = len(
                self.store.list_embeddings(
                    document.id,
                    model=self.embedding_provider.model,
                    dimensions=self.embedding_provider.dimensions,
                )
            )
            model = self.embedding_provider.model
        else:
            embedded_count = 0
            model = None
        return IngestionResult(
            status=IngestionStatus.ALREADY_EXISTS,
            document=document,
            embedded_chunk_count=embedded_count,
            embedding_model=model,
            embedding_input_tokens=input_tokens,
        )

    def _embed_prepared_chunks(
        self,
        chunks: Sequence[PreparedChunk],
    ) -> tuple[tuple[PreparedEmbedding, ...], int]:
        if self.embedding_provider is None:
            return (), 0
        batch = self.embedding_provider.embed([chunk.text for chunk in chunks])
        if (
            len(batch.vectors) != len(chunks)
            or batch.model != self.embedding_provider.model
            or batch.dimensions != self.embedding_provider.dimensions
        ):
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_FAILED,
                "Embedding provider returned an unexpected batch",
            )
        return (
            tuple(
                PreparedEmbedding(
                    sequence=chunk.sequence,
                    model=batch.model,
                    dimensions=batch.dimensions,
                    vector=vector,
                )
                for chunk, vector in zip(chunks, batch.vectors, strict=True)
            ),
            batch.input_tokens,
        )

    def _prepare_hierarchy(
        self,
        normalised: NormalisedDocument,
    ) -> PreparedChunkHierarchy:
        return chunk_document_hierarchy(
            normalised,
            child_size_tokens=self.settings.chunk_size_tokens,
            child_overlap_tokens=self.settings.chunk_overlap_tokens,
            parent_size_tokens=self.settings.parent_chunk_size_tokens,
            token_counter=self.token_counter,
        )

    def _extract(self, document: ValidatedDocument):
        return extract_document(
            document,
            ocr_provider=self.ocr_provider,
            ocr_language=self.settings.ocr_language,
            ocr_dpi=self.settings.ocr_dpi,
            ocr_page_timeout_seconds=self.settings.ocr_page_timeout_seconds,
            ocr_max_pages=self.settings.ocr_max_pages,
            ocr_max_image_pixels=self.settings.ocr_max_image_pixels,
        )

    def _embed_stored_chunks(
        self,
        chunks: Sequence[StoredChunk],
    ) -> tuple[tuple[PreparedEmbedding, ...], int]:
        prepared = tuple(
            PreparedChunk(
                sequence=chunk.sequence,
                text=chunk.text,
                character_count=chunk.character_count,
                location=chunk.location,
                token_count=chunk.token_count,
            )
            for chunk in chunks
        )
        return self._embed_prepared_chunks(prepared)
