"""End-to-end orchestration for local document ingestion."""

from __future__ import annotations

from pathlib import Path

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion.chunking import chunk_document
from maintenance_assistant.ingestion.errors import DuplicateDocumentError
from maintenance_assistant.ingestion.extractors import extract_document
from maintenance_assistant.ingestion.models import IngestionResult, IngestionStatus
from maintenance_assistant.ingestion.normalisation import normalise_document
from maintenance_assistant.ingestion.storage import LocalDocumentStore
from maintenance_assistant.ingestion.validation import validate_document


class IngestionService:
    """Coordinate validation, extraction, chunking and local persistence."""

    def __init__(
        self,
        settings: Settings,
        store: LocalDocumentStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or LocalDocumentStore(settings.data_directory)

    def ingest(self, path: Path) -> IngestionResult:
        """Ingest one local document or return its existing stored record."""

        validated = validate_document(path, self.settings)
        existing = self.store.find_by_hash(validated.content_hash)
        if existing is not None:
            return IngestionResult(
                status=IngestionStatus.ALREADY_EXISTS,
                document=existing,
            )

        extracted = extract_document(validated)
        normalised = normalise_document(extracted)
        chunks = chunk_document(
            normalised,
            chunk_size=self.settings.chunk_size_characters,
            overlap=self.settings.chunk_overlap_characters,
        )
        try:
            stored = self.store.save(extracted, chunks)
        except DuplicateDocumentError as duplicate:
            stored = self.store.get_document(duplicate.document_id)
            if stored is None:
                raise
            return IngestionResult(
                status=IngestionStatus.ALREADY_EXISTS,
                document=stored,
            )
        return IngestionResult(status=IngestionStatus.COMPLETED, document=stored)
