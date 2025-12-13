"""End-to-end orchestration for local document ingestion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
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
    DocumentMetadata,
    NormalisedDocument,
    PreparedChunk,
    PreparedChunkHierarchy,
    PreparedEmbedding,
    ReindexResult,
    StoredChunk,
    StoredDocument,
    ValidatedDocument,
    metadata_embedding_text,
)
from maintenance_assistant.ingestion.normalisation import normalise_document
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.ingestion.validation import validate_document
from maintenance_assistant.ocr import OCRProvider, create_ocr_provider
from maintenance_assistant.vision import (
    VisualAnalysisProvider,
    create_visual_analysis_provider,
)

if TYPE_CHECKING:
    from maintenance_assistant.embeddings import EmbeddingProvider

_CONFIGURED_OCR_PROVIDER = object()
_CONFIGURED_VISUAL_ANALYSIS_PROVIDER = object()


class IngestionService:
    """Coordinate validation, extraction, chunking and local persistence."""

    def __init__(
        self,
        settings: Settings,
        store: LocalDocumentStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        ocr_provider: OCRProvider | None | object = _CONFIGURED_OCR_PROVIDER,
        visual_analysis_provider: VisualAnalysisProvider | None | object = (
            _CONFIGURED_VISUAL_ANALYSIS_PROVIDER
        ),
    ) -> None:
        self.settings = settings
        self.store = store or LocalDocumentStore(settings.data_directory)
        self.embedding_provider = embedding_provider
        self.ocr_provider = (
            create_ocr_provider(settings)
            if ocr_provider is _CONFIGURED_OCR_PROVIDER
            else cast(OCRProvider | None, ocr_provider)
        )
        self.visual_analysis_provider = (
            create_visual_analysis_provider(settings)
            if visual_analysis_provider is _CONFIGURED_VISUAL_ANALYSIS_PROVIDER
            else cast(VisualAnalysisProvider | None, visual_analysis_provider)
        )
        self.token_counter = TiktokenCounter(settings.chunk_token_encoding)

    def ingest(
        self,
        path: Path,
        metadata: DocumentMetadata | None = None,
    ) -> IngestionResult:
        """Ingest one local document or return its existing stored record."""

        validated = validate_document(path, self.settings)
        existing = self.store.find_by_hash(validated.content_hash)
        if existing is not None:
            return self._result_for_existing(existing, metadata)

        selected_metadata = metadata or DocumentMetadata()

        extracted = self._extract(validated)
        normalised = normalise_document(extracted)
        hierarchy = self._prepare_hierarchy(normalised)
        embeddings, input_tokens = self._embed_prepared_chunks(
            hierarchy.children,
            selected_metadata,
        )
        try:
            stored = self.store.save(
                extracted,
                hierarchy.children,
                embeddings,
                parents=hierarchy.parents,
                metadata=selected_metadata,
            )
        except DuplicateDocumentError as duplicate:
            stored = self.store.get_document(duplicate.document_id)
            if stored is None:
                raise
            return self._result_for_existing(stored, selected_metadata)
        return IngestionResult(
            status=IngestionStatus.COMPLETED,
            document=stored,
            embedded_chunk_count=len(embeddings),
            embedding_model=self.embedding_provider.model
            if self.embedding_provider
            else None,
            embedding_input_tokens=input_tokens,
        )

    def ingest_revision(
        self,
        path: Path,
        document_id: str,
        metadata: DocumentMetadata | None = None,
    ) -> IngestionResult:
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
        selected_metadata = metadata if metadata is not None else previous.metadata
        embeddings, input_tokens = self._embed_prepared_chunks(
            hierarchy.children,
            selected_metadata,
        )
        stored = self.store.save(
            extracted,
            hierarchy.children,
            embeddings,
            parents=hierarchy.parents,
            supersedes_document_id=document_id,
            metadata=selected_metadata,
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
        embeddings, input_tokens = self._embed_prepared_chunks(
            hierarchy.children,
            document.metadata,
        )
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

    def update_metadata(
        self,
        document_id: str,
        metadata: DocumentMetadata,
    ) -> StoredDocument:
        """Replace a manual's classifications and refresh metadata-aware vectors."""

        document = self.store.get_document(document_id)
        if document is None:
            raise DocumentLifecycleError(
                DocumentLifecycleErrorCode.DOCUMENT_NOT_FOUND,
                "Document was not found",
            )
        embeddings: tuple[PreparedEmbedding, ...] = ()
        if self.embedding_provider is not None:
            embeddings, _ = self._embed_stored_chunks(
                self.store.list_chunks(document_id),
                metadata,
            )
        return self.store.update_document_metadata(document_id, metadata, embeddings)

    def replace_metadata_option(
        self,
        category: str,
        value: str,
        replacement: str | None,
    ) -> int:
        """Rename, merge or remove a catalogue value across stored manuals."""

        if category not in {"brand", "machine", "site", "document_type"}:
            raise ValueError("Unknown metadata category")
        source = DocumentMetadata(brand=value).brand
        target = DocumentMetadata(brand=replacement).brand if replacement else ()
        if not source:
            raise ValueError("Metadata value must contain text")
        source_key = source[0].casefold()
        replacement_value = target[0] if target else None
        affected = 0
        for document in self.store.list_documents(limit=100_000):
            current = getattr(document.metadata, category)
            if not any(item.casefold() == source_key for item in current):
                continue
            revised: list[str] = []
            for item in current:
                candidate = replacement_value if item.casefold() == source_key else item
                if candidate is not None and candidate.casefold() not in {
                    existing.casefold() for existing in revised
                }:
                    revised.append(candidate)
            updated = replace(document.metadata, **{category: tuple(revised)})
            self.update_metadata(document.id, updated)
            affected += 1
        self.store.remove_metadata_option(category, source[0])
        return affected

    def _result_for_existing(
        self,
        document: StoredDocument,
        requested_metadata: DocumentMetadata | None = None,
    ) -> IngestionResult:
        input_tokens = 0
        target_metadata = requested_metadata or document.metadata
        if target_metadata != document.metadata:
            refreshed: tuple[PreparedEmbedding, ...] = ()
            if self.embedding_provider is not None:
                refreshed, input_tokens = self._embed_stored_chunks(
                    self.store.list_chunks(document.id),
                    target_metadata,
                )
            document = self.store.update_document_metadata(
                document.id,
                target_metadata,
                refreshed,
            )
        if self.embedding_provider is not None:
            missing = self.store.missing_embedding_chunks(
                document.id,
                model=self.embedding_provider.model,
                dimensions=self.embedding_provider.dimensions,
            )
            if missing:
                embeddings, missing_tokens = self._embed_stored_chunks(
                    missing,
                    document.metadata,
                )
                input_tokens += missing_tokens
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
        metadata: DocumentMetadata = DocumentMetadata(),
    ) -> tuple[tuple[PreparedEmbedding, ...], int]:
        if self.embedding_provider is None:
            return (), 0
        batch = self.embedding_provider.embed(
            [metadata_embedding_text(chunk.text, metadata) for chunk in chunks]
        )
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
            visual_analysis_provider=self.visual_analysis_provider,
            visual_analysis_render_dpi=self.settings.visual_analysis_render_dpi,
            visual_analysis_max_pages=self.settings.visual_analysis_max_pages,
            visual_analysis_max_image_pixels=(
                self.settings.visual_analysis_max_image_pixels
            ),
        )

    def _embed_stored_chunks(
        self,
        chunks: Sequence[StoredChunk],
        metadata: DocumentMetadata = DocumentMetadata(),
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
        return self._embed_prepared_chunks(prepared, metadata)
