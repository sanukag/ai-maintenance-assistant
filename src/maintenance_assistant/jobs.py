"""Persistent background ingestion queue shared by the API and worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
from pathlib import Path
import logging
import shutil
import sqlite3
from time import sleep
from typing import Callable
from uuid import uuid4

from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import create_embedding_provider
from maintenance_assistant.ingestion import DocumentMetadata, IngestionService, LocalDocumentStore
from maintenance_assistant.ingestion.errors import IngestionError
from maintenance_assistant.ocr import create_ocr_provider
from maintenance_assistant.vision import create_visual_analysis_provider
from maintenance_assistant.vector_index import create_vector_index

logger = logging.getLogger(__name__)


class IngestionJobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class IngestionJob:
    id: str
    original_filename: str
    staged_path: Path
    metadata: DocumentMetadata
    status: IngestionJobStatus
    stage: str
    progress: int
    attempts: int
    document_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobCancelled(Exception):
    """Stop a job cooperatively between expensive ingestion stages."""


class IngestionJobStore:
    """Coordinate durable uploads and atomic worker claims through SQLite."""

    def __init__(self, store: LocalDocumentStore) -> None:
        self.store = store
        self.jobs_directory = store.data_directory / "jobs"

    def enqueue(
        self,
        source: Path,
        original_filename: str,
        metadata: DocumentMetadata,
    ) -> IngestionJob:
        self.store.initialise()
        self.jobs_directory.mkdir(parents=True, exist_ok=True)
        job_id = str(uuid4())
        suffix = source.suffix.lower()
        destination = self.jobs_directory / f"{job_id}{suffix}"
        shutil.copy2(source, destination)
        now = _now()
        try:
            with self.store._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO ingestion_jobs (
                        id, original_filename, staged_path, metadata_json,
                        status, stage, progress, attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'queued', 'Waiting for worker', 0, 0, ?, ?)
                    """,
                    (job_id, original_filename, str(destination), _metadata_json(metadata), now, now),
                )
        except sqlite3.Error:
            destination.unlink(missing_ok=True)
            raise
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> IngestionJob | None:
        self.store.initialise()
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _job_from_row(row) if row else None

    def list(self, limit: int = 50) -> tuple[IngestionJob, ...]:
        self.store.initialise()
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def claim_next(self) -> IngestionJob | None:
        self.store.initialise()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM ingestion_jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = _now()
            connection.execute(
                """
                UPDATE ingestion_jobs SET status = 'processing', stage = 'Starting',
                    progress = 2, attempts = attempts + 1, started_at = ?,
                    completed_at = NULL, error_code = NULL, error_message = NULL,
                    updated_at = ? WHERE id = ? AND status = 'queued'
                """,
                (now, now, row["id"]),
            )
        return self.get(row["id"])

    def update_progress(self, job_id: str, stage: str, progress: int) -> None:
        with self.store._connection() as connection:
            connection.execute(
                """UPDATE ingestion_jobs SET stage = ?, progress = ?, updated_at = ?
                   WHERE id = ? AND status IN ('processing', 'cancel_requested')""",
                (stage, progress, _now(), job_id),
            )

    def cancellation_requested(self, job_id: str) -> bool:
        job = self.get(job_id)
        return job is not None and job.status is IngestionJobStatus.CANCEL_REQUESTED

    def cancel(self, job_id: str) -> IngestionJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job.status is IngestionJobStatus.QUEUED:
            self._finish(job_id, IngestionJobStatus.CANCELLED, "Cancelled", 0)
        elif job.status is IngestionJobStatus.PROCESSING:
            with self.store._connection() as connection:
                connection.execute(
                    "UPDATE ingestion_jobs SET status = 'cancel_requested', stage = 'Cancelling', updated_at = ? WHERE id = ?",
                    (_now(), job_id),
                )
        return self.get(job_id)

    def retry(self, job_id: str) -> IngestionJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job.status not in {IngestionJobStatus.FAILED, IngestionJobStatus.CANCELLED}:
            raise ValueError("Only failed or cancelled jobs can be retried")
        if not job.staged_path.is_file():
            raise ValueError("The staged upload is no longer available")
        with self.store._connection() as connection:
            connection.execute(
                """UPDATE ingestion_jobs SET status = 'queued', stage = 'Waiting for worker',
                   progress = 0, error_code = NULL, error_message = NULL,
                   started_at = NULL, completed_at = NULL, updated_at = ? WHERE id = ?""",
                (_now(), job_id),
            )
        return self.get(job_id)

    def complete(self, job_id: str, document_id: str) -> None:
        job = self.get(job_id)
        self._finish(job_id, IngestionJobStatus.COMPLETED, "Complete", 100, document_id=document_id)
        if job is not None:
            job.staged_path.unlink(missing_ok=True)

    def fail(self, job_id: str, error: Exception) -> None:
        code = error.code.value if isinstance(error, IngestionError) else "ingestion_failed"
        if isinstance(error, IngestionError):
            message = error.message
        else:
            logger.exception("Background ingestion failed", exc_info=error)
            message = "The ingestion worker could not process this upload"
        self._finish(job_id, IngestionJobStatus.FAILED, "Failed", 100, code, message)

    def mark_cancelled(self, job_id: str) -> None:
        self._finish(job_id, IngestionJobStatus.CANCELLED, "Cancelled", 0)

    def recover_interrupted(self) -> int:
        self.store.initialise()
        with self.store._connection() as connection:
            return connection.execute(
                """UPDATE ingestion_jobs SET status = 'queued', stage = 'Recovered after restart',
                   progress = 0, started_at = NULL, updated_at = ?
                   WHERE status IN ('processing', 'cancel_requested')""",
                (_now(),),
            ).rowcount

    def _finish(
        self,
        job_id: str,
        status: IngestionJobStatus,
        stage: str,
        progress: int,
        error_code: str | None = None,
        error_message: str | None = None,
        document_id: str | None = None,
    ) -> None:
        now = _now()
        with self.store._connection() as connection:
            connection.execute(
                """UPDATE ingestion_jobs SET status = ?, stage = ?, progress = ?,
                   document_id = ?, error_code = ?, error_message = ?,
                   completed_at = ?, updated_at = ? WHERE id = ?""",
                (status.value, stage, progress, document_id, error_code, error_message, now, now, job_id),
            )


class IngestionWorker:
    """Process one persistent job at a time with cooperative cancellation."""

    def __init__(self, jobs: IngestionJobStore, ingestion: IngestionService) -> None:
        self.jobs = jobs
        self.ingestion = ingestion

    def run_once(self) -> bool:
        job = self.jobs.claim_next()
        if job is None:
            return False

        def report(stage: str, progress: int) -> None:
            if progress < 100 and self.jobs.cancellation_requested(job.id):
                raise JobCancelled
            self.jobs.update_progress(job.id, stage, progress)

        try:
            result = self.ingestion.ingest(job.staged_path, job.metadata, progress=report)
        except JobCancelled:
            self.jobs.mark_cancelled(job.id)
        except Exception as error:
            self.jobs.fail(job.id, error)
        else:
            self.jobs.complete(job.id, result.document.id)
        return True


def run_worker(settings: Settings, poll_seconds: float = 1.0, once: bool = False) -> None:
    store = LocalDocumentStore(settings.data_directory)
    jobs = IngestionJobStore(store)
    vector_index = create_vector_index(settings, store)
    ingestion = IngestionService(
        settings,
        store=store,
        embedding_provider=create_embedding_provider(settings),
        ocr_provider=create_ocr_provider(settings),
        visual_analysis_provider=create_visual_analysis_provider(settings),
        vector_index=vector_index,
    )
    if vector_index is not None:
        try:
            vector_index.rebuild()
        except Exception:
            logger.exception("Qdrant bootstrap failed; SQLite fallback remains active")
    jobs.recover_interrupted()
    worker = IngestionWorker(jobs, ingestion)
    while True:
        worked = worker.run_once()
        if once:
            return
        if not worked:
            sleep(poll_seconds)


def _metadata_json(metadata: DocumentMetadata) -> str:
    return json.dumps({name: list(getattr(metadata, name)) for name in ("brand", "machine", "site", "document_type")})


def _job_from_row(row: sqlite3.Row) -> IngestionJob:
    metadata = json.loads(row["metadata_json"])
    return IngestionJob(
        id=row["id"], original_filename=row["original_filename"], staged_path=Path(row["staged_path"]),
        metadata=DocumentMetadata(**metadata), status=IngestionJobStatus(row["status"]),
        stage=row["stage"], progress=row["progress"], attempts=row["attempts"],
        document_id=row["document_id"], error_code=row["error_code"], error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
