"""Persistent queue and worker behaviour."""

from pathlib import Path

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import DocumentMetadata, IngestionService, LocalDocumentStore
from maintenance_assistant.jobs import IngestionJobStatus, IngestionJobStore, IngestionWorker
from tests.fakes import KeywordEmbeddingProvider


def _queue(tmp_path: Path) -> tuple[IngestionJobStore, Path]:
    store = LocalDocumentStore(tmp_path / "data")
    source = tmp_path / "pump.txt"
    source.write_text("Inspect the pump seal before starting.", encoding="utf-8")
    return IngestionJobStore(store), source


def test_worker_completes_a_persisted_job_and_removes_staged_upload(tmp_path: Path) -> None:
    jobs, source = _queue(tmp_path)
    job = jobs.enqueue(source, source.name, DocumentMetadata(brand="Acme"))
    service = IngestionService(
        Settings(data_directory=tmp_path / "data"),
        store=jobs.store,
        embedding_provider=KeywordEmbeddingProvider(),
        ocr_provider=None,
        visual_analysis_provider=None,
    )

    assert IngestionWorker(jobs, service).run_once() is True

    completed = jobs.get(job.id)
    assert completed is not None
    assert completed.status is IngestionJobStatus.COMPLETED
    assert completed.progress == 100
    assert completed.stage == "Complete"
    assert completed.attempts == 1
    assert completed.document_id is not None
    assert not job.staged_path.exists()
    assert jobs.store.get_document(completed.document_id).metadata.brand == ("Acme",)  # type: ignore[union-attr]


def test_cancelled_queued_job_can_be_retried(tmp_path: Path) -> None:
    jobs, source = _queue(tmp_path)
    job = jobs.enqueue(source, source.name, DocumentMetadata())

    cancelled = jobs.cancel(job.id)
    retried = jobs.retry(job.id)

    assert cancelled is not None and cancelled.status is IngestionJobStatus.CANCELLED
    assert job.staged_path.is_file()
    assert retried is not None and retried.status is IngestionJobStatus.QUEUED


def test_worker_records_safe_failure_details_and_keeps_upload(tmp_path: Path) -> None:
    jobs, source = _queue(tmp_path)
    job = jobs.enqueue(source, source.name, DocumentMetadata())

    class BrokenIngestion:
        def ingest(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("provider unavailable")

    IngestionWorker(jobs, BrokenIngestion()).run_once()  # type: ignore[arg-type]
    failed = jobs.get(job.id)

    assert failed is not None
    assert failed.status is IngestionJobStatus.FAILED
    assert failed.error_code == "ingestion_failed"
    assert failed.error_message == "The ingestion worker could not process this upload"
    assert job.staged_path.is_file()


def test_interrupted_jobs_are_recovered_for_a_restarted_worker(tmp_path: Path) -> None:
    jobs, source = _queue(tmp_path)
    job = jobs.enqueue(source, source.name, DocumentMetadata())
    assert jobs.claim_next() is not None

    assert jobs.recover_interrupted() == 1
    recovered = jobs.get(job.id)
    assert recovered is not None
    assert recovered.status is IngestionJobStatus.QUEUED
    assert recovered.stage == "Recovered after restart"
