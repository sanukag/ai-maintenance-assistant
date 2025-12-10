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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKER_AVAILABLE = shutil.which("docker") is not None
RUN_CONTAINER_TESTS = os.environ.get("AMA_RUN_CONTAINER_TESTS") == "1"


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker CLI is not installed")
def test_compose_configuration_is_valid() -> None:
    configured = _compose("config", "--format", "json")
    configuration = json.loads(configured.stdout)
    api = configuration["services"]["api"]

    assert api["read_only"] is True
    assert api["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in api["security_opt"]
    assert api["environment"]["AMA_DATA_DIRECTORY"] == "/app/data"
    assert api["environment"]["AMA_ANSWER_PROVIDER"] in {"none", "openai"}
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


@pytest.mark.container
@pytest.mark.skipif(
    not DOCKER_AVAILABLE or not RUN_CONTAINER_TESTS,
    reason="Docker and AMA_RUN_CONTAINER_TESTS=1 are required",
)
def test_container_runs_as_non_root_and_preserves_documents() -> None:
    project = f"ama-test-{uuid4().hex[:8]}"
    port = _available_port()
    environment = os.environ | {
        "AMA_API_PORT": str(port),
        "AMA_EMBEDDING_PROVIDER": "none",
        "AMA_ANSWER_PROVIDER": "none",
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
        assert _json_request(f"{base_url}/health")["status"] == "ok"

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

        ingested = _upload_text_document(base_url)
        document_id = ingested["document"]["id"]

        _compose(*compose, "restart", "api", environment=environment)
        _wait_until_healthy(base_url)
        documents = _json_request(f"{base_url}/documents")
        assert [item["id"] for item in documents["items"]] == [document_id]
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


def _upload_text_document(base_url: str) -> dict[str, object]:
    boundary = f"ama-{uuid4().hex}"
    content = b"Pump isolation procedure."
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="manual.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = Request(
        f"{base_url}/documents",
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
