"""HTTP routes for ingestion, browsing and semantic retrieval."""

from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from maintenance_assistant.api.errors import ApiError
from maintenance_assistant.api.schemas import (
    AnswerRequest,
    AnswerResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    CredentialListResponse,
    CredentialStatusResponse,
    CredentialUpdateRequest,
    DiagnosticSessionCreateRequest,
    DiagnosticSessionListResponse,
    DiagnosticSessionResponse,
    DiagnosticSessionSummaryResponse,
    DiagnosticTurnRequest,
    DocumentListResponse,
    DocumentMetadataRequest,
    DocumentResponse,
    HealthResponse,
    IngestionResponse,
    IngestionJobResponse,
    IngestionJobListResponse,
    DocumentMetadataResponse,
    MetadataOptionsResponse,
    MetadataOptionChangeRequest,
    MetadataOptionChangeResponse,
    RuntimeMetricsResponse,
    ReindexResponse,
    ResponseFeedbackRequest,
    ResponseFeedbackResponse,
    RevisionHistoryResponse,
    VectorIndexRebuildResponse,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from maintenance_assistant.api.services import ApiServices, get_services
from maintenance_assistant.credentials import CredentialName, CredentialStore
from maintenance_assistant.ingestion import (
    DocumentLifecycleStatus,
    DocumentMetadata,
    IngestionStatus,
)

router = APIRouter()
_UPLOAD_BLOCK_SIZE = 1024 * 1024


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(services: ApiServices = Depends(get_services)) -> HealthResponse:
    """Confirm that the API and local store are ready."""

    services.store.initialise()
    provider = services.embedding_provider
    ocr_provider = services.ocr_provider
    visual_analysis_provider = services.visual_analysis_provider
    answer_provider = services.answer_provider
    return HealthResponse(
        status="ok",
        storage="ok",
        ocr=(
            "disabled"
            if ocr_provider is None
            else "available"
            if ocr_provider.available
            else "unavailable"
        ),
        ocr_engine=ocr_provider.name if ocr_provider is not None else None,
        ocr_version=ocr_provider.version if ocr_provider is not None else None,
        visual_analysis=(
            "disabled"
            if visual_analysis_provider is None
            else "available"
            if visual_analysis_provider.available
            else "unavailable"
        ),
        visual_analysis_model=(
            visual_analysis_provider.model
            if visual_analysis_provider is not None
            else None
        ),
        embeddings="enabled" if provider is not None else "disabled",
        embedding_model=provider.model if provider is not None else None,
        answers="enabled" if services.answers is not None else "disabled",
        answer_model=answer_provider.model if answer_provider is not None else None,
        diagnostics="enabled" if services.diagnostics is not None else "disabled",
        diagnostic_model=(
            services.diagnostic_provider.model
            if services.diagnostic_provider is not None
            else None
        ),
        vector_store=services.settings.vector_store,
        vector_index=(
            "disabled"
            if services.vector_index is None
            else "available"
            if services.vector_index.available()
            else "unavailable"
        ),
        reranking="enabled" if services.reranker is not None else "disabled",
        rerank_model=services.reranker.model if services.reranker is not None else None,
    )


@router.get("/metrics", response_model=RuntimeMetricsResponse, tags=["system"])
def runtime_metrics(
    request: Request,
    services: ApiServices = Depends(get_services),
) -> RuntimeMetricsResponse:
    """Return aggregate timings and local storage performance state."""

    snapshot = request.app.state.runtime_metrics.snapshot()
    snapshot["embedding_cache"] = {
        **services.store.embedding_cache_stats(),
        "maximum_entries": services.settings.embedding_cache_max_entries,
    }
    snapshot["sqlite"] = services.store.sqlite_runtime()
    return RuntimeMetricsResponse.model_validate(snapshot)


def _credential_environment(request: Request) -> dict[CredentialName, str | None]:
    settings = request.app.state.base_settings
    return {CredentialName.OPENAI_API_KEY: settings.openai_api_key}


@router.get("/credentials", response_model=CredentialListResponse, tags=["system"])
def list_credentials(request: Request) -> CredentialListResponse:
    """Return masked credential status without exposing secret values."""

    credentials: CredentialStore = request.app.state.credential_store
    statuses = credentials.list_statuses(_credential_environment(request))
    return CredentialListResponse(
        items=[CredentialStatusResponse.from_status(item) for item in statuses]
    )


@router.put(
    "/credentials/{credential_name}",
    response_model=CredentialStatusResponse,
    tags=["system"],
)
def save_credential(
    credential_name: CredentialName,
    payload: CredentialUpdateRequest,
    request: Request,
) -> CredentialStatusResponse:
    """Encrypt an API key and reload external services immediately."""

    credentials: CredentialStore = request.app.state.credential_store
    status = credentials.save(credential_name, payload.value.get_secret_value())
    request.app.state.reload_services()
    return CredentialStatusResponse.from_status(status)


@router.delete(
    "/credentials/{credential_name}",
    response_model=CredentialStatusResponse,
    tags=["system"],
)
def delete_credential(
    credential_name: CredentialName,
    request: Request,
) -> CredentialStatusResponse:
    """Delete a saved key and fall back to environment configuration if present."""

    credentials: CredentialStore = request.app.state.credential_store
    current = credentials.status(
        credential_name,
        _credential_environment(request).get(credential_name),
    )
    if not current.can_delete:
        if current.source == "environment":
            raise ApiError(
                409,
                "credential_environment_managed",
                "This API key is supplied by the service environment and cannot be deleted here",
            )
        raise ApiError(404, "credential_not_found", "No saved API key was found")
    credentials.delete(credential_name)
    request.app.state.reload_services()
    refreshed = credentials.status(
        credential_name,
        _credential_environment(request).get(credential_name),
    )
    return CredentialStatusResponse.from_status(refreshed)


@router.post(
    "/documents",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["documents"],
)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    brand: list[str] | None = Form(default=None),
    machine: list[str] | None = Form(default=None),
    site: list[str] | None = Form(default=None),
    document_type: list[str] | None = Form(default=None),
    services: ApiServices = Depends(get_services),
) -> IngestionResponse:
    """Upload a supported document and run the complete ingestion pipeline."""

    filename = _safe_filename(file.filename)
    maximum_bytes = services.settings.max_document_size_mb * 1024 * 1024
    metadata = _document_metadata(brand, machine, site, document_type)
    try:
        with tempfile.TemporaryDirectory(prefix="ama-upload-") as temporary_directory:
            upload_path = Path(temporary_directory) / filename
            await _write_upload(file, upload_path, maximum_bytes)
            result = await run_in_threadpool(
                services.ingestion.ingest,
                upload_path,
                metadata,
            )
    finally:
        await file.close()

    if result.status is IngestionStatus.ALREADY_EXISTS:
        response.status_code = status.HTTP_200_OK
    return IngestionResponse.from_result(result)


@router.post(
    "/ingestion-jobs",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["documents"],
)
async def queue_document(
    file: UploadFile = File(...),
    brand: list[str] | None = Form(default=None),
    machine: list[str] | None = Form(default=None),
    site: list[str] | None = Form(default=None),
    document_type: list[str] | None = Form(default=None),
    services: ApiServices = Depends(get_services),
) -> IngestionJobResponse:
    """Persist an upload and return immediately while a worker ingests it."""

    filename = _safe_filename(file.filename)
    maximum_bytes = services.settings.max_document_size_mb * 1024 * 1024
    metadata = _document_metadata(brand, machine, site, document_type)
    try:
        with tempfile.TemporaryDirectory(prefix="ama-job-upload-") as temporary_directory:
            upload_path = Path(temporary_directory) / filename
            await _write_upload(file, upload_path, maximum_bytes)
            job = await run_in_threadpool(
                services.jobs.enqueue,
                upload_path,
                filename,
                metadata or DocumentMetadata(),
            )
    finally:
        await file.close()
    return IngestionJobResponse.from_job(job)


@router.get(
    "/ingestion-jobs",
    response_model=IngestionJobListResponse,
    tags=["documents"],
)
def list_ingestion_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    services: ApiServices = Depends(get_services),
) -> IngestionJobListResponse:
    return IngestionJobListResponse(
        items=[IngestionJobResponse.from_job(job) for job in services.jobs.list(limit)]
    )


@router.get(
    "/ingestion-jobs/{job_id}",
    response_model=IngestionJobResponse,
    tags=["documents"],
)
def get_ingestion_job(
    job_id: str,
    services: ApiServices = Depends(get_services),
) -> IngestionJobResponse:
    job = services.jobs.get(job_id)
    if job is None:
        raise ApiError(404, "ingestion_job_not_found", "Ingestion job was not found")
    return IngestionJobResponse.from_job(job)


@router.post(
    "/ingestion-jobs/{job_id}/cancel",
    response_model=IngestionJobResponse,
    tags=["documents"],
)
def cancel_ingestion_job(
    job_id: str,
    services: ApiServices = Depends(get_services),
) -> IngestionJobResponse:
    job = services.jobs.cancel(job_id)
    if job is None:
        raise ApiError(404, "ingestion_job_not_found", "Ingestion job was not found")
    return IngestionJobResponse.from_job(job)


@router.post(
    "/ingestion-jobs/{job_id}/retry",
    response_model=IngestionJobResponse,
    tags=["documents"],
)
def retry_ingestion_job(
    job_id: str,
    services: ApiServices = Depends(get_services),
) -> IngestionJobResponse:
    try:
        job = services.jobs.retry(job_id)
    except ValueError as error:
        raise ApiError(409, "ingestion_job_not_retryable", str(error)) from error
    if job is None:
        raise ApiError(404, "ingestion_job_not_found", "Ingestion job was not found")
    return IngestionJobResponse.from_job(job)


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
    "/metadata/options",
    response_model=MetadataOptionsResponse,
    tags=["documents"],
)
def list_metadata_options(
    services: ApiServices = Depends(get_services),
) -> MetadataOptionsResponse:
    """List current manual classifications for upload and retrieval dropdowns."""

    return MetadataOptionsResponse.from_metadata(
        services.store.list_metadata_options()
    )


@router.patch(
    "/metadata/options/{category}/{value}",
    response_model=MetadataOptionChangeResponse,
    tags=["documents"],
)
def change_metadata_option(
    category: str,
    value: str,
    request: MetadataOptionChangeRequest,
    services: ApiServices = Depends(get_services),
) -> MetadataOptionChangeResponse:
    """Rename, merge or remove a reusable metadata value everywhere it is used."""

    try:
        affected = services.ingestion.replace_metadata_option(
            category,
            value,
            request.replacement,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_metadata_option", str(error)) from error
    return MetadataOptionChangeResponse(
        affected_documents=affected,
        options=MetadataOptionsResponse.from_metadata(
            services.store.list_metadata_options()
        ),
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


@router.patch(
    "/documents/{document_id}/metadata",
    response_model=DocumentResponse,
    tags=["documents"],
)
def update_document_metadata(
    document_id: str,
    request: DocumentMetadataRequest,
    services: ApiServices = Depends(get_services),
) -> DocumentResponse:
    """Edit a manual's classifications and refresh its metadata-aware vectors."""

    return DocumentResponse.from_document(
        services.ingestion.update_metadata(document_id, request.as_metadata())
    )


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
    brand: list[str] | None = Form(default=None),
    machine: list[str] | None = Form(default=None),
    site: list[str] | None = Form(default=None),
    document_type: list[str] | None = Form(default=None),
    services: ApiServices = Depends(get_services),
) -> IngestionResponse:
    """Add a new revision and supersede the selected current manual."""

    filename = _safe_filename(file.filename)
    maximum_bytes = services.settings.max_document_size_mb * 1024 * 1024
    metadata = _document_metadata(brand, machine, site, document_type)
    try:
        with tempfile.TemporaryDirectory(prefix="ama-revision-") as temporary_directory:
            upload_path = Path(temporary_directory) / filename
            await _write_upload(file, upload_path, maximum_bytes)
            result = await run_in_threadpool(
                services.ingestion.ingest_revision,
                upload_path,
                document_id,
                metadata,
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

    document = services.store.archive_document(document_id)
    services.ingestion.synchronise_vector_index(document_id)
    return DocumentResponse.from_document(document)


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


@router.post(
    "/vector-index/rebuild",
    response_model=VectorIndexRebuildResponse,
    tags=["system"],
)
def rebuild_vector_index(
    services: ApiServices = Depends(get_services),
) -> VectorIndexRebuildResponse:
    """Recreate Qdrant from durable SQLite vectors after an outage or migration."""

    if services.vector_index is None:
        raise ApiError(409, "vector_index_disabled", "Qdrant vector storage is not enabled")
    try:
        count = services.vector_index.rebuild()
    except Exception as error:
        raise ApiError(503, "vector_index_unavailable", "Qdrant could not rebuild the vector index") from error
    return VectorIndexRebuildResponse(indexed_chunks=count)


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
    services.ingestion.remove_from_vector_index(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchResponse, tags=["search"])
def search_documents(
    request: SearchRequest,
    services: ApiServices = Depends(get_services),
) -> SearchResponse:
    """Hybrid-search chunks and return traceable ranked results."""

    if services.search is None:
        raise ApiError(
            503,
            "embeddings_disabled",
            "Hybrid search requires an enabled embedding provider",
        )
    results = services.search.search(
        request.query,
        limit=request.limit,
        document_id=request.document_id,
        metadata=request.as_metadata(),
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
    if (
        request.conversation_id is not None
        and services.conversations.get_conversation(request.conversation_id) is None
    ):
        raise ApiError(
            404,
            "conversation_not_found",
            "Conversation was not found",
        )
    answer = services.answers.answer(
        request.question,
        max_sources=request.max_sources,
        document_id=request.document_id,
        metadata=request.as_metadata(),
    )
    try:
        conversation = services.conversations.record_exchange(
            answer,
            conversation_id=request.conversation_id,
            scope_document_id=request.document_id,
            scope_metadata=request.as_metadata(),
        )
    except KeyError as error:
        raise ApiError(
            404,
            "conversation_not_found",
            "Conversation was not found",
        ) from error
    return AnswerResponse.from_answer(answer, conversation.conversation.id)


@router.post(
    "/diagnostic-sessions",
    response_model=DiagnosticSessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["diagnostics"],
)
def create_diagnostic_session(
    request: DiagnosticSessionCreateRequest,
    services: ApiServices = Depends(get_services),
) -> DiagnosticSessionResponse:
    """Start a grounded, stateful fault investigation."""

    if services.diagnostics is None:
        raise ApiError(
            503,
            "diagnostics_disabled",
            "Guided diagnostics require enabled embedding and answer services",
        )
    try:
        detail = services.diagnostics.start_session(
            request.message,
            document_id=request.document_id,
            metadata=request.as_metadata(),
            safety_status=request.safety_status,
        )
    except KeyError as error:
        raise ApiError(404, "document_not_found", "Document was not found") from error
    return DiagnosticSessionResponse.from_detail(detail)


@router.post(
    "/diagnostic-sessions/{session_id}/turns",
    response_model=DiagnosticSessionResponse,
    tags=["diagnostics"],
)
def continue_diagnostic_session(
    session_id: str,
    request: DiagnosticTurnRequest,
    services: ApiServices = Depends(get_services),
) -> DiagnosticSessionResponse:
    """Apply one worker observation or question to an existing investigation."""

    if services.diagnostics is None:
        raise ApiError(503, "diagnostics_disabled", "Guided diagnostics are not enabled")
    try:
        detail = services.diagnostics.continue_session(
            session_id,
            request.message,
            safety_status=request.safety_status,
        )
    except KeyError as error:
        raise ApiError(404, "diagnostic_session_not_found", "Diagnostic session was not found") from error
    return DiagnosticSessionResponse.from_detail(detail)


@router.get(
    "/diagnostic-sessions",
    response_model=DiagnosticSessionListResponse,
    tags=["diagnostics"],
)
def list_diagnostic_sessions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    services: ApiServices = Depends(get_services),
) -> DiagnosticSessionListResponse:
    sessions = services.diagnostic_store.list_sessions(limit=limit, offset=offset)
    return DiagnosticSessionListResponse(
        items=[DiagnosticSessionSummaryResponse.from_session(item) for item in sessions],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/diagnostic-sessions/{session_id}",
    response_model=DiagnosticSessionResponse,
    tags=["diagnostics"],
)
def get_diagnostic_session(
    session_id: str,
    services: ApiServices = Depends(get_services),
) -> DiagnosticSessionResponse:
    detail = services.diagnostic_store.get_session(session_id)
    if detail is None:
        raise ApiError(404, "diagnostic_session_not_found", "Diagnostic session was not found")
    return DiagnosticSessionResponse.from_detail(detail)


@router.delete(
    "/diagnostic-sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["diagnostics"],
)
def delete_diagnostic_session(
    session_id: str,
    services: ApiServices = Depends(get_services),
) -> Response:
    if not services.diagnostic_store.delete_session(session_id):
        raise ApiError(404, "diagnostic_session_not_found", "Diagnostic session was not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    tags=["conversations"],
)
def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    services: ApiServices = Depends(get_services),
) -> ConversationListResponse:
    """List locally stored conversations from newest to oldest."""

    conversations = services.conversations.list_conversations(
        limit=limit,
        offset=offset,
    )
    return ConversationListResponse(
        items=[
            ConversationSummaryResponse.from_conversation(conversation)
            for conversation in conversations
        ],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    tags=["conversations"],
)
def get_conversation(
    conversation_id: str,
    services: ApiServices = Depends(get_services),
) -> ConversationResponse:
    """Return every stored message in one conversation."""

    conversation = services.conversations.get_conversation(conversation_id)
    if conversation is None:
        raise ApiError(404, "conversation_not_found", "Conversation was not found")
    return ConversationResponse.from_detail(conversation)


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=ResponseFeedbackResponse,
    tags=["conversations"],
)
def set_response_feedback(
    conversation_id: str,
    message_id: str,
    request: ResponseFeedbackRequest,
    services: ApiServices = Depends(get_services),
) -> ResponseFeedbackResponse:
    """Create or replace the worker rating for one assistant response."""

    try:
        rating = services.conversations.set_response_feedback(
            conversation_id,
            message_id,
            request.rating,
        )
    except KeyError as error:
        raise ApiError(404, "response_not_found", "Assistant response was not found") from error
    except ValueError as error:
        raise ApiError(422, "feedback_not_allowed", str(error)) from error
    return ResponseFeedbackResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        rating=rating,
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversations"],
)
def clear_response_feedback(
    conversation_id: str,
    message_id: str,
    services: ApiServices = Depends(get_services),
) -> Response:
    """Clear a worker rating without deleting the assistant response."""

    try:
        services.conversations.clear_response_feedback(conversation_id, message_id)
    except KeyError as error:
        raise ApiError(404, "response_not_found", "Assistant response was not found") from error
    except ValueError as error:
        raise ApiError(422, "feedback_not_allowed", str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversations"],
)
def delete_conversation(
    conversation_id: str,
    services: ApiServices = Depends(get_services),
) -> Response:
    """Permanently delete one conversation and all of its messages."""

    if not services.conversations.delete_conversation(conversation_id):
        raise ApiError(404, "conversation_not_found", "Conversation was not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _safe_filename(uploaded_name: str | None) -> str:
    normalised = (uploaded_name or "").replace("\\", "/")
    filename = normalised.rsplit("/", maxsplit=1)[-1].strip()
    if filename in {"", ".", ".."}:
        raise ApiError(422, "missing_filename", "Uploaded file must have a filename")
    return filename


def _document_metadata(
    brand: list[str] | None,
    machine: list[str] | None,
    site: list[str] | None,
    document_type: list[str] | None,
) -> DocumentMetadata | None:
    if all(value is None for value in (brand, machine, site, document_type)):
        return None
    try:
        return DocumentMetadata(
            brand=brand,
            machine=machine,
            site=site,
            document_type=document_type,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_metadata", str(error)) from error


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
