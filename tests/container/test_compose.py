from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from tests.ingestion.pdf_factory import write_scanned_pdf
from tests.fakes import KeywordEmbeddingProvider
from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import DocumentMetadata, IngestionService, LocalDocumentStore
from maintenance_assistant.vector_index import QdrantVectorIndex

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKER_AVAILABLE = shutil.which("docker") is not None
RUN_CONTAINER_TESTS = os.environ.get("AMA_RUN_CONTAINER_TESTS") == "1"


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker CLI is not installed")
def test_compose_configuration_is_valid() -> None:
    configured = _compose("config", "--format", "json")
    configuration = json.loads(configured.stdout)
    api = configuration["services"]["api"]
    qdrant = configuration["services"]["qdrant"]

    assert qdrant["image"] == "qdrant/qdrant:v1.18.2-unprivileged"
    assert qdrant["read_only"] is True
    assert qdrant["cap_drop"] == ["ALL"]
    assert qdrant["healthcheck"]["test"][0] == "CMD-SHELL"
    assert qdrant["ports"] == [
        {
            "mode": "ingress",
            "target": 6333,
            "published": "6333",
            "protocol": "tcp",
            "host_ip": "127.0.0.1",
        }
    ]

    assert api["read_only"] is True
    assert api["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in api["security_opt"]
    assert api["environment"]["AMA_DATA_DIRECTORY"] == "/app/data"
    assert api["environment"]["AMA_OCR_PROVIDER"] in {"none", "tesseract"}
    assert "AMA_ANSWER_PROVIDER" not in api["environment"]
    assert "AMA_VISUAL_ANALYSIS_PROVIDER" not in api["environment"]
    assert "AMA_EMBEDDING_PROVIDER" not in api["environment"]
    assert api["environment"]["AMA_VECTOR_STORE"] in {"sqlite", "qdrant"}
    assert api["environment"]["AMA_QDRANT_URL"] == "http://qdrant:6333"
    assert int(api["environment"]["AMA_OCR_DPI"]) >= 150
    assert int(api["environment"]["AMA_VISUAL_ANALYSIS_RENDER_DPI"]) >= 100
    assert int(api["environment"]["AMA_ANSWER_MAX_OUTPUT_TOKENS"]) > 0
    assert api["ports"] == [
        {
            "mode": "ingress",
            "target": 8000,
            "published": "8000",
            "protocol": "tcp",
            "host_ip": "127.0.0.1",
        }
    ]
    assert any(
        volume["type"] == "volume" and volume["target"] == "/app/data"
        for volume in api["volumes"]
    )
    assert api["healthcheck"]["test"][0] == "CMD"
    assert api["depends_on"]["qdrant"]["condition"] == "service_healthy"
    worker = configuration["services"]["worker"]
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert worker["command"][0] == "ama-worker"
    assert worker["environment"]["AMA_DATA_DIRECTORY"] == "/app/data"
    assert worker["depends_on"]["api"]["condition"] == "service_healthy"
    assert worker["healthcheck"]["test"][0] == "CMD-SHELL"
    web = configuration["services"]["web"]
    assert web["read_only"] is True
    assert web["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in web["security_opt"]
    assert web["environment"]["AMA_API_BASE_URL"] == "http://api:8000"
    assert web["ports"] == [
        {
            "mode": "ingress",
            "target": 3000,
            "published": "3000",
            "protocol": "tcp",
            "host_ip": "127.0.0.1",
        }
    ]
    assert web["depends_on"]["api"]["condition"] == "service_healthy"
    assert web["healthcheck"]["test"][0] == "CMD"


@pytest.mark.container
@pytest.mark.skipif(
    not DOCKER_AVAILABLE or not RUN_CONTAINER_TESTS,
    reason="Docker and AMA_RUN_CONTAINER_TESTS=1 are required",
)
def test_container_runs_as_non_root_and_preserves_documents(tmp_path: Path) -> None:
    project = f"ama-test-{uuid4().hex[:8]}"
    port = _available_port()
    web_port = _available_port()
    while web_port == port:
        web_port = _available_port()
    qdrant_port = _available_port()
    while qdrant_port in {port, web_port}:
        qdrant_port = _available_port()
    environment = os.environ | {
        "AMA_API_PORT": str(port),
        "AMA_WEB_PORT": str(web_port),
        "AMA_QDRANT_PORT": str(qdrant_port),
    }
    environment.pop("OPENAI_API_KEY", None)
    compose = ("--project-name", project)

    try:
        _compose(
            *compose,
            "up",
            "--build",
            "--detach",
            "--wait",
            "--wait-timeout",
            "120",
            environment=environment,
            timeout=180,
        )
        base_url = f"http://127.0.0.1:{port}"
        web_url = f"http://127.0.0.1:{web_port}"
        health = _json_request(f"{base_url}/health")
        assert health["status"] == "ok"
        assert health["ocr"] == "available"
        assert health["ocr_engine"] == "tesseract"
        assert health["visual_analysis"] == "disabled"
        assert health["vector_store"] == "qdrant"
        assert health["vector_index"] == "available"
        assert _json_request(f"{base_url}/conversations")["items"] == []
        assert "Ask your manuals" in _text_request(web_url)
        assert _json_request(f"{web_url}/api/backend/health")["status"] == "ok"

        user = _compose(
            *compose,
            "exec",
            "-T",
            "api",
            "id",
            "-u",
            environment=environment,
        )
        assert user.stdout.strip() == "10001"
        web_user = _compose(
            *compose,
            "exec",
            "-T",
            "web",
            "id",
            "-u",
            environment=environment,
        )
        assert web_user.stdout.strip() == "10001"
        worker_user = _compose(
            *compose,
            "exec",
            "-T",
            "worker",
            "id",
            "-u",
            environment=environment,
        )
        assert worker_user.stdout.strip() == "10001"
        qdrant_user = _compose(
            *compose,
            "exec",
            "-T",
            "qdrant",
            "id",
            "-u",
            environment=environment,
        )
        assert qdrant_user.stdout.strip() != "0"

        vector_source = tmp_path / "indexed-pump.txt"
        vector_source.write_text("Pump seal replacement procedure.", encoding="utf-8")
        vector_settings = Settings(data_directory=tmp_path / "vector-data")
        vector_provider = KeywordEmbeddingProvider()
        vector_store = LocalDocumentStore(vector_settings.data_directory)
        indexed_document = IngestionService(
            vector_settings,
            store=vector_store,
            embedding_provider=vector_provider,
        ).ingest(vector_source, DocumentMetadata(brand="Acme")).document
        index = QdrantVectorIndex(
            vector_store,
            url=f"http://127.0.0.1:{qdrant_port}",
            model=vector_provider.model,
            dimensions=vector_provider.dimensions,
        )
        assert index.rebuild() == indexed_document.chunk_count
        indexed_matches = index.search(
            (1.0, 0.0, 0.0),
            limit=3,
            document_id=indexed_document.id,
            metadata=DocumentMetadata(brand="Acme"),
        )
        assert indexed_matches[0][0] == vector_store.list_chunks(indexed_document.id)[0].id

        scanned_path = tmp_path / "scanned-procedure.pdf"
        write_scanned_pdf(
            scanned_path,
            "PUMP ISOLATION PROCEDURE\nClose valve V1 before maintenance",
        )
        scanned = _upload_binary_document(
            base_url,
            filename="scanned-procedure.pdf",
            content=scanned_path.read_bytes(),
            content_type="application/pdf",
            path="/ingestion-jobs",
        )
        completed_job = _wait_for_job(base_url, str(scanned["id"]))
        scanned_document_id = str(completed_job["document_id"])
        scanned_document = _json_request(f"{base_url}/documents/{scanned_document_id}")
        assert str(scanned_document["extractor_name"]).startswith("pypdf+tesseract")
        assert scanned_document["page_count"] == 1
        assert int(scanned_document["chunk_count"]) >= 1

        ingested = _upload_text_document(
            base_url,
            filename="manual-v1.txt",
            content=b"Pump isolation procedure.",
        )
        first_document_id = ingested["document"]["id"]
        replacement = _upload_text_document(
            base_url,
            path=f"/documents/{first_document_id}/revisions",
            filename="manual-v2.txt",
            content=b"Updated pump isolation procedure.",
        )
        current_document_id = replacement["document"]["id"]

        _compose(*compose, "restart", "api", environment=environment)
        _wait_until_healthy(base_url)
        documents = _json_request(f"{base_url}/documents")
        assert _json_request(f"{base_url}/conversations")["items"] == []
        assert [item["id"] for item in documents["items"]] == [
            current_document_id,
            first_document_id,
            scanned_document_id,
        ]
        assert [item["lifecycle_status"] for item in documents["items"]] == [
            "current",
            "superseded",
            "current",
        ]
        deleted = Request(
            f"{web_url}/api/backend/documents/{first_document_id}",
            method="DELETE",
        )
        with urlopen(deleted, timeout=5) as response:
            assert response.status == 204
        archived = Request(
            f"{web_url}/api/backend/documents/{current_document_id}/archive",
            data=b"",
            method="POST",
        )
        assert _json_request(archived.full_url, archived)["lifecycle_status"] == (
            "archived"
        )
        current = _json_request(
            f"{base_url}/documents?lifecycle_status=current"
        )
        assert [item["id"] for item in current["items"]] == [scanned_document_id]

        saved_credential = _json_api_request(
            f"{base_url}/credentials/OPENAI_API_KEY",
            method="PUT",
            body={"value": "sk-container-persisted-1234"},
        )
        assert saved_credential["masked_value"] == "••••1234"
        assert _json_request(f"{base_url}/health")["answers"] == "enabled"
        key_mode = _compose(
            *compose,
            "exec",
            "-T",
            "api",
            "stat",
            "-c",
            "%a",
            "/app/data/credential-encryption.key",
            environment=environment,
        )
        assert key_mode.stdout.strip() == "600"

        _compose(*compose, "restart", "api", environment=environment)
        _wait_until_healthy(base_url)
        persisted_credential = _json_request(f"{base_url}/credentials")["items"][0]
        assert persisted_credential["source"] == "saved"
        assert persisted_credential["masked_value"] == "••••1234"
        deleted_credential = _json_api_request(
            f"{web_url}/api/backend/credentials/OPENAI_API_KEY",
            method="DELETE",
        )
        assert deleted_credential["source"] == "missing"
        assert _json_request(f"{base_url}/health")["answers"] == "disabled"
    finally:
        _compose(
            *compose,
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
            environment=environment,
            check=False,
            timeout=120,
        )


def _compose(
    *arguments: str,
    environment: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["docker", "compose", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            "Docker Compose command failed:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _json_request(url: str, request: Request | None = None) -> dict[str, object]:
    with urlopen(request or url, timeout=5) as response:
        return json.loads(response.read())


def _text_request(url: str) -> str:
    with urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


def _json_api_request(
    url: str,
    *,
    method: str,
    body: dict[str, str] | None = None,
) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    return _json_request(url, request)


def _upload_text_document(
    base_url: str,
    *,
    filename: str,
    content: bytes,
    path: str = "/documents",
) -> dict[str, object]:
    boundary = f"ama-{uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        f"{base_url}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return _json_request(request.full_url, request)


def _upload_binary_document(
    base_url: str,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    path: str = "/documents",
) -> dict[str, object]:
    boundary = f"ama-{uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        f"{base_url}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return _json_request(request.full_url, request)


def _wait_until_healthy(base_url: str, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _json_request(f"{base_url}/health")["status"] == "ok":
                return
        except (ConnectionError, TimeoutError, URLError):
            pass
        time.sleep(0.25)
    raise AssertionError("Container did not become healthy after restart")


def _wait_for_job(base_url: str, job_id: str, timeout: int = 60) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _json_request(f"{base_url}/ingestion-jobs/{job_id}")
        if job["status"] == "completed":
            return job
        if job["status"] == "failed":
            raise AssertionError(f"Background ingestion failed: {job['error_message']}")
        time.sleep(0.25)
    raise AssertionError("Background ingestion did not complete")
