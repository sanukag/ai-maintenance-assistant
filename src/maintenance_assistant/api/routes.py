"""HTTP routes for ingestion, browsing and semantic retrieval."""

from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from maintenance_assistant.api.errors import ApiError
from maintenance_assistant.api.schemas import (
    AnswerRequest,
    AnswerResponse,
    DocumentListResponse,
    DocumentResponse,
    HealthResponse,
    IngestionResponse,
    ReindexResponse,
    RevisionHistoryResponse,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from maintenance_assistant.api.services import ApiServices, get_services
from maintenance_assistant.ingestion import DocumentLifecycleStatus, IngestionStatus

router = APIRouter()
_UPLOAD_BLOCK_SIZE = 1024 * 1024


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(services: ApiServices = Depends(get_services)) -> HealthResponse:
    """Confirm that the API and local store are ready."""

    services.store.initialise()
    provider = services.embedding_provider
    answer_provider = services.answer_provider
    return HealthResponse(
        status="ok",
        storage="ok",
        embeddings="enabled" if provider is not None else "disabled",
        embedding_model=provider.model if provider is not None else None,
        answers="enabled" if services.answers is not None else "disabled",
        answer_model=answer_provider.model if answer_provider is not None else None,
    )


@router.post(
    "/documents",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["documents"],
)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    services: ApiServices = Depends(get_services),
) -> IngestionResponse:
    """Upload a supported document and run the complete ingestion pipeline."""

    filename = _safe_filename(file.filename)
    maximum_bytes = services.settings.max_document_size_mb * 1024 * 1024
    try:
        with tempfile.TemporaryDirectory(prefix="ama-upload-") as temporary_directory:
            upload_path = Path(temporary_directory) / filename
            await _write_upload(file, upload_path, maximum_bytes)
            result = await run_in_threadpool(services.ingestion.ingest, upload_path)
    finally:
        await file.close()

    if result.status is IngestionStatus.ALREADY_EXISTS:
        response.status_code = status.HTTP_200_OK
    return IngestionResponse.from_result(result)


@router.get("/documents", response_model=DocumentListResponse, tags=["documents"])
def list_documents(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    lifecycle_status: DocumentLifecycleStatus | None = Query(default=None),
    services: ApiServices = Depends(get_services),
) -> DocumentListResponse:
    """List locally stored documents from newest to oldest."""

    documents = services.store.list_documents(
        limit=limit,
        offset=offset,
        lifecycle_status=lifecycle_status,
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_document(document) for document in documents],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    tags=["documents"],
)
def get_document(
    document_id: str,
    services: ApiServices = Depends(get_services),
) -> DocumentResponse:
    """Return one locally stored document."""

    document = services.store.get_document(document_id)
    if document is None:
        raise ApiError(404, "document_not_found", "Document was not found")
    return DocumentResponse.from_document(document)


@router.get(
    "/documents/{document_id}/revisions",
    response_model=RevisionHistoryResponse,
    tags=["documents"],
)
def list_document_revisions(
    document_id: str,
    services: ApiServices = Depends(get_services),
) -> RevisionHistoryResponse:
    """Return every retained revision for one manual."""

    revisions = services.store.list_revision_history(document_id)
    return RevisionHistoryResponse(
        items=[DocumentResponse.from_document(item) for item in revisions]
    )


@router.post(
    "/documents/{document_id}/revisions",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["documents"],
)
async def replace_document(
    document_id: str,
    file: UploadFile = File(...),
    services: ApiServices = Depends(get_services),
) -> IngestionResponse:
    """Add a new revision and supersede the selected current manual."""

    filename = _safe_filename(file.filename)
    maximum_bytes = services.settings.max_document_size_mb * 1024 * 1024
    try:
        with tempfile.TemporaryDirectory(prefix="ama-revision-") as temporary_directory:
            upload_path = Path(temporary_directory) / filename
            await _write_upload(file, upload_path, maximum_bytes)
            result = await run_in_threadpool(
                services.ingestion.ingest_revision,
                upload_path,
                document_id,
            )
    finally:
        await file.close()
    return IngestionResponse.from_result(result)


@router.post(
    "/documents/{document_id}/archive",
    response_model=DocumentResponse,
    tags=["documents"],
)
def archive_document(
    document_id: str,
    services: ApiServices = Depends(get_services),
) -> DocumentResponse:
    """Archive a manual and exclude it from future retrieval."""

    return DocumentResponse.from_document(services.store.archive_document(document_id))


@router.post(
    "/documents/{document_id}/reindex",
    response_model=ReindexResponse,
    tags=["documents"],
)
def reindex_document(
    document_id: str,
    services: ApiServices = Depends(get_services),
) -> ReindexResponse:
    """Regenerate every vector using the active embedding configuration."""

    if services.embedding_provider is None:
        raise ApiError(
            503,
            "embeddings_disabled",
            "Re-indexing requires an enabled embedding provider",
        )
    return ReindexResponse.from_result(services.ingestion.reindex(document_id))


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["documents"],
)
def delete_document(
    document_id: str,
    services: ApiServices = Depends(get_services),
) -> Response:
    """Permanently delete a manual and all of its locally stored data."""

    services.store.delete_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchResponse, tags=["search"])
def search_documents(
    request: SearchRequest,
    services: ApiServices = Depends(get_services),
) -> SearchResponse:
    """Search embedded chunks and return traceable ranked results."""

    if services.search is None:
        raise ApiError(
            503,
            "embeddings_disabled",
            "Semantic search requires an enabled embedding provider",
        )
    results = services.search.search(
        request.query,
        limit=request.limit,
        document_id=request.document_id,
    )
    return SearchResponse(
        results=[SearchResultResponse.from_result(result) for result in results]
    )


@router.post("/answers", response_model=AnswerResponse, tags=["answers"])
def answer_question(
    request: AnswerRequest,
    services: ApiServices = Depends(get_services),
) -> AnswerResponse:
    """Answer a question using retrieved local evidence and verified citations."""

    if services.answers is None:
        raise ApiError(
            503,
            "answers_disabled",
            "Grounded answers require enabled embedding and answer providers",
        )
    answer = services.answers.answer(
        request.question,
        max_sources=request.max_sources,
        document_id=request.document_id,
    )
    return AnswerResponse.from_answer(answer)


def _safe_filename(uploaded_name: str | None) -> str:
    normalised = (uploaded_name or "").replace("\\", "/")
    filename = normalised.rsplit("/", maxsplit=1)[-1].strip()
    if filename in {"", ".", ".."}:
        raise ApiError(422, "missing_filename", "Uploaded file must have a filename")
    return filename


async def _write_upload(
    upload: UploadFile,
    destination: Path,
    maximum_bytes: int,
) -> None:
    written = 0
    try:
        with destination.open("xb") as target:
            while block := await upload.read(_UPLOAD_BLOCK_SIZE):
                written += len(block)
                if written > maximum_bytes:
                    raise ApiError(
                        413,
                        "file_too_large",
                        f"Document exceeds the {maximum_bytes // (1024 * 1024)} MB limit",
                    )
                target.write(block)
    except ApiError:
        raise
    except OSError as error:
        raise ApiError(
            500,
            "upload_failed",
            "Uploaded document could not be prepared for ingestion",
        ) from error
