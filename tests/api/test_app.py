from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from maintenance_assistant.api.__main__ import main
from maintenance_assistant.api.app import _ingestion_status, create_app
from maintenance_assistant.api.errors import ApiError
from maintenance_assistant.api.routes import _safe_filename
from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import IngestionErrorCode
from maintenance_assistant.jobs import IngestionWorker
from tests.fakes import (
    FixedAnswerProvider,
    FixedOCRProvider,
    FixedVisualAnalysisProvider,
    KeywordEmbeddingProvider,
)
from tests.ingestion.pdf_factory import write_diagram_pdf, write_scanned_image


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_directory": tmp_path / "data",
        "chunk_size_tokens": 7,
        "chunk_overlap_tokens": 0,
    }
    values.update(overrides)
    return Settings(**values)


def test_health_reports_local_services(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path, ocr_provider="none"),
        embedding_provider=None,
        ocr_provider=None,
    )

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "storage": "ok",
        "ocr": "disabled",
        "ocr_engine": None,
        "ocr_version": None,
        "visual_analysis": "disabled",
        "visual_analysis_model": None,
        "embeddings": "disabled",
        "embedding_model": None,
        "answers": "disabled",
        "answer_model": None,
        "vector_store": "sqlite",
        "vector_index": "disabled",
        "reranking": "disabled",
        "rerank_model": None,
    }
    assert (tmp_path / "data" / "maintenance-assistant.db").is_file()


def test_metrics_report_templated_routes_cache_and_sqlite_runtime(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path, ocr_provider="none"),
        embedding_provider=None,
        ocr_provider=None,
    )

    with TestClient(application) as client:
        client.get("/health")
        client.get("/documents/missing")
        response = client.get("/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["requests_total"] == 2
    assert body["embedding_cache"] == {
        "entries": 0,
        "hits": 0,
        "maximum_entries": 10_000,
    }
    assert body["sqlite"] == {
        "journal_mode": "wal",
        "synchronous": 1,
        "busy_timeout_ms": 5_000,
    }
    assert any(
        row["method"] == "GET" and row["route"] == "/documents/{document_id}"
        for row in body["routes"]
    )


def test_api_keys_can_be_saved_edited_deleted_and_reused_after_restart(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, ocr_provider="none")
    first_application = create_app(
        settings=settings,
        ocr_provider=None,
    )

    with TestClient(first_application) as client:
        missing = client.get("/credentials")
        saved = client.put(
            "/credentials/OPENAI_API_KEY",
            json={"value": "sk-project-first-1234"},
        )
        enabled = client.get("/health")

    assert missing.json()["items"][0]["source"] == "missing"
    assert saved.status_code == 200
    assert saved.json()["masked_value"] == "••••1234"
    assert saved.json()["can_delete"] is True
    assert "sk-project-first-1234" not in saved.text
    assert enabled.json()["embeddings"] == "enabled"
    assert enabled.json()["visual_analysis"] == "available"
    assert enabled.json()["reranking"] == "enabled"
    assert enabled.json()["answers"] == "enabled"

    restarted_application = create_app(settings=settings, ocr_provider=None)
    with TestClient(restarted_application) as client:
        persisted = client.get("/credentials")
        edited = client.put(
            "/credentials/OPENAI_API_KEY",
            json={"value": "sk-project-edited-9876"},
        )
        deleted = client.delete("/credentials/OPENAI_API_KEY")
        disabled = client.get("/health")

    assert persisted.json()["items"][0]["source"] == "saved"
    assert edited.json()["masked_value"] == "••••9876"
    assert deleted.json()["source"] == "missing"
    assert deleted.json()["configured"] is False
    assert disabled.json()["embeddings"] == "disabled"
    assert disabled.json()["visual_analysis"] == "disabled"
    assert disabled.json()["reranking"] == "disabled"
    assert disabled.json()["answers"] == "disabled"


def test_environment_api_key_can_be_overridden_but_not_deleted_from_settings(
    tmp_path: Path,
) -> None:
    application = create_app(
        settings=_settings(
            tmp_path,
            ocr_provider="none",
            openai_api_key="sk-environment-key-1111",
        ),
        ocr_provider=None,
    )

    with TestClient(application) as client:
        environment = client.get("/credentials").json()["items"][0]
        rejected = client.delete("/credentials/OPENAI_API_KEY")
        client.put(
            "/credentials/OPENAI_API_KEY",
            json={"value": "sk-saved-override-2222"},
        )
        fallback = client.delete("/credentials/OPENAI_API_KEY")

    assert environment["source"] == "environment"
    assert environment["masked_value"] == "••••1111"
    assert environment["can_delete"] is False
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "credential_environment_managed"
    assert fallback.json()["source"] == "environment"
    assert fallback.json()["masked_value"] == "••••1111"


@pytest.mark.parametrize(
    "payload",
    [{"value": "short"}, {"value": "has whitespace"}, {"value": "x" * 2_049}],
)
def test_api_key_endpoint_rejects_invalid_secrets(
    tmp_path: Path,
    payload: dict[str, str],
) -> None:
    application = create_app(settings=_settings(tmp_path), ocr_provider=None)

    with TestClient(application) as client:
        response = client.put("/credentials/OPENAI_API_KEY", json=payload)

    assert response.status_code == 422


def test_upload_scanned_image_reports_local_ocr_metadata(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    write_scanned_image(image, "CHECK MOTOR ROTATION")
    ocr = FixedOCRProvider("CHECK MOTOR ROTATION")
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=None,
        ocr_provider=ocr,
    )

    with TestClient(application) as client:
        health = client.get("/health")
        uploaded = client.post(
            "/documents",
            files={"file": ("scan.png", image.read_bytes(), "image/png")},
        )

    assert health.json()["ocr"] == "available"
    assert health.json()["ocr_engine"] == "test-ocr"
    assert uploaded.status_code == 201
    assert uploaded.json()["document"]["format"] == "image"
    assert uploaded.json()["document"]["extractor_name"] == "test-ocr"
    assert uploaded.json()["document"]["chunk_count"] == 1


def test_upload_diagram_reports_visual_analysis_and_embeds_description(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pump-diagram.pdf"
    write_diagram_pdf(path)
    vision = FixedVisualAnalysisProvider()
    embeddings = KeywordEmbeddingProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=embeddings,
        visual_analysis_provider=vision,
    )

    with TestClient(application) as client:
        health = client.get("/health")
        uploaded = client.post(
            "/documents",
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
        )
        searched = client.post(
            "/search",
            json={"query": "Relationships and flow", "limit": 10},
        )

    assert health.json()["visual_analysis"] == "available"
    assert health.json()["visual_analysis_model"] == "test-vision-model"
    assert uploaded.status_code == 201
    assert uploaded.json()["document"]["extractor_name"] == (
        "pypdf+test-vision"
    )
    assert uploaded.json()["embeddings"]["chunk_count"] >= 1
    assert searched.status_code == 200
    assert any(
        "Visual analysis" in item["chunk"]["text"]
        for item in searched.json()["results"]
    )


def test_upload_browse_and_search_document(tmp_path: Path) -> None:
    provider = KeywordEmbeddingProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=provider,
    )

    with TestClient(application) as client:
        health = client.get("/health")
        uploaded = client.post(
            "/documents",
            files={
                "file": (
                    "../../procedures.txt",
                    b"Pump seal replacement.\n\nValve isolation procedure.",
                    "text/plain",
                )
            },
        )
        document_id = uploaded.json()["document"]["id"]
        listing = client.get("/documents", params={"limit": 1, "offset": 0})
        detail = client.get(f"/documents/{document_id}")
        search = client.post(
            "/search",
            json={"query": "  How do I repair the pump?  ", "limit": 1},
        )

    assert health.json()["embeddings"] == "enabled"
    assert health.json()["embedding_model"] == "test-embedding"
    assert uploaded.status_code == 201
    assert uploaded.json()["status"] == "completed"
    assert uploaded.json()["document"]["original_filename"] == "procedures.txt"
    assert uploaded.json()["document"]["chunk_count"] == 2
    assert uploaded.json()["document"]["lifecycle_status"] == "current"
    assert uploaded.json()["document"]["revision"] == 1
    assert uploaded.json()["document"]["supersedes_document_id"] is None
    assert uploaded.json()["embeddings"] == {
        "chunk_count": 2,
        "model": "test-embedding",
        "input_tokens": 6,
    }
    assert listing.json()["items"] == [uploaded.json()["document"]]
    assert listing.json()["limit"] == 1
    assert detail.json() == uploaded.json()["document"]
    assert search.status_code == 200
    assert search.json()["results"][0]["document"]["id"] == document_id
    assert search.json()["results"][0]["chunk"]["text"] == "Pump seal replacement."
    assert search.json()["results"][0]["chunk"]["token_count"] == 5
    parent = search.json()["results"][0]["parent_context"]
    assert "Pump seal replacement." in parent["text"]
    assert "Valve isolation procedure." in parent["text"]
    assert parent["token_count"] > search.json()["results"][0]["chunk"]["token_count"]
    assert search.json()["results"][0]["score"] == pytest.approx(1.0)
    assert search.json()["results"][0]["retrieval_methods"] == ["semantic", "text"]
    assert search.json()["results"][0]["semantic_score"] == pytest.approx(1.0)
    assert search.json()["results"][0]["lexical_score"] is not None
    assert search.json()["results"][0]["fusion_score"] == pytest.approx(1.0)
    assert search.json()["results"][0]["rerank_score"] is None
    assert provider.calls[-1] == ("How do I repair the pump?",)


def test_repeated_upload_returns_existing_document(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
    )
    upload = {"file": ("manual.txt", b"Pump inspection procedure.", "text/plain")}

    with TestClient(application) as client:
        first = client.post("/documents", files=upload)
        second = client.post("/documents", files=upload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "already_exists"
    assert second.json()["document"]["id"] == first.json()["document"]["id"]


def test_background_upload_can_be_polled_cancelled_retried_and_completed(
    tmp_path: Path,
) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
    )
    with TestClient(application) as client:
        queued = client.post(
            "/ingestion-jobs",
            files={"file": ("pump.txt", b"Inspect the pump seal.", "text/plain")},
            data={"brand": ["Acme", "Northwind"]},
        )
        unclassified = client.post(
            "/ingestion-jobs",
            files={"file": ("plain.txt", b"Plain procedure.", "text/plain")},
        )
        job_id = queued.json()["id"]
        cancelled = client.post(f"/ingestion-jobs/{job_id}/cancel")
        retried = client.post(f"/ingestion-jobs/{job_id}/retry")
        assert IngestionWorker(
            application.state.services.jobs,
            application.state.services.ingestion,
        ).run_once()
        completed = client.get(f"/ingestion-jobs/{job_id}")
        recent = client.get("/ingestion-jobs")

    assert queued.status_code == 202
    assert unclassified.status_code == 202
    assert unclassified.json()["metadata"]["brand"] == []
    assert queued.json()["status"] == "queued"
    assert queued.json()["metadata"]["brand"] == ["Acme", "Northwind"]
    assert cancelled.json()["status"] == "cancelled"
    assert retried.json()["status"] == "queued"
    assert completed.json()["status"] == "completed"
    assert completed.json()["document_id"] is not None
    assert job_id in {item["id"] for item in recent.json()["items"]}


def test_metadata_flows_through_upload_options_search_answers_and_history(
    tmp_path: Path,
) -> None:
    embeddings = KeywordEmbeddingProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=embeddings,
        answer_provider=FixedAnswerProvider(),
    )

    with TestClient(application) as client:
        acme = client.post(
            "/documents",
            files={"file": ("acme.txt", b"Pump isolation procedure.", "text/plain")},
            data={
                "brand": ["  Acme  ", "Acme Industrial"],
                "machine": ["P-100", "P-100 Mk II"],
                "site": "North plant",
                "document_type": "Service manual",
            },
        )
        beta = client.post(
            "/documents",
            files={"file": ("beta.txt", b"Pump inspection checklist.", "text/plain")},
            data={"brand": "Beta", "machine": "P-200", "site": "South plant"},
        )
        options = client.get("/metadata/options")
        searched = client.post(
            "/search",
            json={"query": "pump", "brand": "acme", "machine": ["P-100"]},
        )
        searched_across_brands = client.post(
            "/search",
            json={"query": "pump", "brand": ["Acme Industrial", "Beta"]},
        )
        answered = client.post(
            "/answers",
            json={
                "question": "How do I isolate the pump?",
                "brand": "Acme",
                "site": "North plant",
            },
        )
        history = client.get(
            f"/conversations/{answered.json()['conversation_id']}"
        )
        client.post(f"/documents/{beta.json()['document']['id']}/archive")
        client.delete(f"/documents/{beta.json()['document']['id']}")
        retained_options = client.get("/metadata/options")

    metadata = acme.json()["document"]["metadata"]
    assert metadata == {
        "brand": ["Acme", "Acme Industrial"],
        "machine": ["P-100", "P-100 Mk II"],
        "site": ["North plant"],
        "document_type": ["Service manual"],
    }
    assert options.json()["brand"] == ["Acme", "Acme Industrial", "Beta"]
    assert options.json()["machine"] == ["P-100", "P-100 Mk II", "P-200"]
    assert "Beta" in retained_options.json()["brand"]
    assert {item["document"]["id"] for item in searched.json()["results"]} == {
        acme.json()["document"]["id"]
    }
    assert len(searched_across_brands.json()["results"]) == 2
    assert any("Brand: acme" in call[0] for call in embeddings.calls)
    assert answered.status_code == 200
    assert history.json()["messages"][0]["scope_metadata"] == {
        "brand": ["Acme"],
        "machine": [],
        "site": ["North plant"],
        "document_type": [],
    }


def test_metadata_can_be_edited_renamed_merged_and_removed(tmp_path: Path) -> None:
    embeddings = KeywordEmbeddingProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=embeddings,
    )
    with TestClient(application) as client:
        first = client.post(
            "/documents",
            files={"file": ("one.txt", b"First pump procedure.", "text/plain")},
            data={"brand": ["Acme", "Legacy"], "machine": "P-100"},
        ).json()["document"]
        second = client.post(
            "/documents",
            files={"file": ("two.txt", b"Second pump procedure.", "text/plain")},
            data={"brand": "Legacy", "machine": "P-200"},
        ).json()["document"]

        edited = client.patch(
            f"/documents/{first['id']}/metadata",
            json={"brand": ["Acme", "Northwind"], "machine": ["P-100"]},
        )
        merged = client.patch(
            "/metadata/options/brand/Legacy",
            json={"replacement": "Northwind"},
        )
        removed = client.patch(
            "/metadata/options/machine/P-200",
            json={"replacement": None},
        )
        second_after = client.get(f"/documents/{second['id']}").json()
        options = client.get("/metadata/options").json()

    assert edited.status_code == 200
    assert edited.json()["metadata"]["brand"] == ["Acme", "Northwind"]
    assert merged.json()["affected_documents"] == 1
    assert removed.json()["affected_documents"] == 1
    assert second_after["metadata"] == {
        "brand": ["Northwind"],
        "machine": [],
        "site": [],
        "document_type": [],
    }
    assert options["brand"] == ["Acme", "Northwind"]
    assert "P-200" not in options["machine"]
    assert any("Brand: Northwind" in call[0] for call in embeddings.calls)


def test_upload_rejects_invalid_metadata(tmp_path: Path) -> None:
    application = create_app(settings=_settings(tmp_path), embedding_provider=None)

    with TestClient(application) as client:
        response = client.post(
            "/documents",
            files={"file": ("manual.txt", b"Pump procedure.", "text/plain")},
            data={"brand": "Acme\nInjected"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_metadata"


@pytest.mark.parametrize(
    ("filename", "content", "expected_status", "expected_code"),
    [
        ("manual.csv", b"pump,inspection", 415, "unsupported_type"),
        ("manual.txt", b"", 422, "empty_file"),
    ],
)
def test_upload_returns_structured_ingestion_errors(
    tmp_path: Path,
    filename: str,
    content: bytes,
    expected_status: int,
    expected_code: str,
) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=None,
    )

    with TestClient(application) as client:
        response = client.post(
            "/documents",
            files={"file": (filename, content, "application/octet-stream")},
        )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


def test_upload_rejects_oversized_body_before_ingestion(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path, max_document_size_mb=1),
        embedding_provider=None,
    )

    with TestClient(application) as client:
        response = client.post(
            "/documents",
            files={"file": ("manual.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert not (tmp_path / "data").exists()


def test_document_detail_reports_missing_record(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=None,
    )

    with TestClient(application) as client:
        response = client.get("/documents/not-present")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "document_not_found",
            "message": "Document was not found",
        }
    }


def test_search_requires_embeddings_and_valid_query(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=None,
    )

    with TestClient(application) as client:
        disabled = client.post("/search", json={"query": "pump"})
        invalid = client.post("/search", json={"query": "   "})

    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "embeddings_disabled"
    assert invalid.status_code == 422


def test_openapi_describes_the_initial_api_surface(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=None,
    )

    with TestClient(application) as client:
        schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "AI Maintenance Assistant API"
    assert set(schema["paths"]) == {
        "/credentials",
        "/credentials/{credential_name}",
        "/metrics",
        "/health",
        "/ingestion-jobs",
        "/ingestion-jobs/{job_id}",
        "/ingestion-jobs/{job_id}/cancel",
        "/ingestion-jobs/{job_id}/retry",
        "/metadata/options",
        "/metadata/options/{category}/{value}",
        "/documents",
        "/documents/{document_id}",
        "/documents/{document_id}/metadata",
        "/documents/{document_id}/archive",
        "/documents/{document_id}/reindex",
        "/documents/{document_id}/revisions",
        "/search",
        "/vector-index/rebuild",
        "/answers",
        "/conversations",
        "/conversations/{conversation_id}",
        "/conversations/{conversation_id}/messages/{message_id}/feedback",
    }


def test_vector_index_rebuild_requires_qdrant_mode(tmp_path: Path) -> None:
    application = create_app(settings=_settings(tmp_path), embedding_provider=None)
    with TestClient(application) as client:
        response = client.post("/vector-index/rebuild")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "vector_index_disabled"


def test_manual_revision_archive_reindex_and_delete_workflow(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
    )

    with TestClient(application) as client:
        first = client.post(
            "/documents",
            files={"file": ("pump-v1.txt", b"Old pump procedure.", "text/plain")},
        )
        first_id = first.json()["document"]["id"]
        replacement = client.post(
            f"/documents/{first_id}/revisions",
            files={
                "file": (
                    "pump-v2.txt",
                    b"Updated pump isolation procedure.",
                    "text/plain",
                )
            },
        )
        second_id = replacement.json()["document"]["id"]
        history = client.get(f"/documents/{second_id}/revisions")
        current = client.get(
            "/documents", params={"lifecycle_status": "current"}
        )
        search = client.post("/search", json={"query": "pump", "limit": 5})
        reindexed = client.post(f"/documents/{second_id}/reindex")
        archived = client.post(f"/documents/{second_id}/archive")
        archived_search = client.post(
            "/search", json={"query": "pump", "limit": 5}
        )
        deleted = client.delete(f"/documents/{second_id}")
        missing = client.get(f"/documents/{second_id}")

    assert replacement.status_code == 201
    assert replacement.json()["document"]["revision"] == 2
    assert replacement.json()["document"]["supersedes_document_id"] == first_id
    assert [item["lifecycle_status"] for item in history.json()["items"]] == [
        "superseded",
        "current",
    ]
    assert [item["id"] for item in current.json()["items"]] == [second_id]
    assert {item["document"]["id"] for item in search.json()["results"]} == {
        second_id
    }
    assert reindexed.json()["embeddings"]["chunk_count"] == replacement.json()[
        "document"
    ]["chunk_count"]
    assert archived.json()["lifecycle_status"] == "archived"
    assert archived_search.json()["results"] == []
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_replacement_rejects_identical_or_non_current_manual(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
    )
    file = ("pump.txt", b"Pump procedure.", "text/plain")

    with TestClient(application) as client:
        uploaded = client.post("/documents", files={"file": file})
        document_id = uploaded.json()["document"]["id"]
        identical = client.post(
            f"/documents/{document_id}/revisions", files={"file": file}
        )
        client.post(f"/documents/{document_id}/archive")
        archived = client.post(
            f"/documents/{document_id}/revisions",
            files={"file": ("pump-v2.txt", b"New pump procedure.", "text/plain")},
        )

    assert identical.status_code == 409
    assert identical.json()["error"]["code"] == "identical_revision"
    assert archived.status_code == 409
    assert archived.json()["error"]["code"] == "revision_conflict"


def test_reindex_requires_embedding_provider(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=None,
    )

    with TestClient(application) as client:
        uploaded = client.post(
            "/documents",
            files={"file": ("pump.txt", b"Pump procedure.", "text/plain")},
        )
        response = client.post(
            f"/documents/{uploaded.json()['document']['id']}/reindex"
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "embeddings_disabled"


def test_answer_endpoint_returns_verified_traceable_citations(tmp_path: Path) -> None:
    embeddings = KeywordEmbeddingProvider()
    answers = FixedAnswerProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=embeddings,
        answer_provider=answers,
    )

    with TestClient(application) as client:
        health = client.get("/health")
        uploaded = client.post(
            "/documents",
            files={
                "file": (
                    "pump-manual.txt",
                    b"Pump isolation procedure.\n\nValve inspection procedure.",
                    "text/plain",
                )
            },
        )
        response = client.post(
            "/answers",
            json={"question": "  How do I maintain the pump?  ", "max_sources": 1},
        )

    assert health.json()["answers"] == "enabled"
    assert health.json()["answer_model"] == "test-answer"
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]
    assert body["question"] == "How do I maintain the pump?"
    assert body["answerable"] is True
    assert body["answer"] == "Isolate the pump before maintenance [S1]."
    assert body["model"] == "test-answer"
    assert body["usage"] == {"input_tokens": 24, "output_tokens": 8}
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["source_id"] == "S1"
    assert citation["document"]["id"] == uploaded.json()["document"]["id"]
    assert citation["chunk_sequence"] == 0
    assert citation["chunk_id"]
    assert citation["parent_context_id"]
    assert "Pump isolation procedure." in citation["excerpt"]
    assert "Valve inspection procedure." in citation["excerpt"]
    assert "stored_path" not in citation["document"]
    assert answers.calls[0][0] == "How do I maintain the pump?"


def test_answer_endpoint_persists_continues_lists_and_deletes_conversations(
    tmp_path: Path,
) -> None:
    answers = FixedAnswerProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
        answer_provider=answers,
    )

    with TestClient(application) as client:
        uploaded = client.post(
            "/documents",
            files={"file": ("manual.txt", b"Pump isolation procedure.", "text/plain")},
        )
        first = client.post(
            "/answers",
            json={"question": "How do I isolate the pump?"},
        )
        conversation_id = first.json()["conversation_id"]
        second = client.post(
            "/answers",
            json={
                "question": "What should I inspect afterwards?",
                "conversation_id": conversation_id,
            },
        )
        conversations = client.get("/conversations")
        client.delete(f"/documents/{uploaded.json()['document']['id']}")
        detail = client.get(f"/conversations/{conversation_id}")
        deleted = client.delete(f"/conversations/{conversation_id}")
        missing = client.get(f"/conversations/{conversation_id}")

    assert second.json()["conversation_id"] == conversation_id
    assert conversations.json()["items"][0]["message_count"] == 4
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[0]["content"] == "How do I isolate the pump?"
    assert messages[1]["content"] == "Isolate the pump before maintenance [S1]."
    assert messages[1]["model"] == "test-answer"
    assert messages[1]["usage"] == {"input_tokens": 24, "output_tokens": 8}
    assert messages[1]["citations"][0]["document_title"] == "manual"
    assert messages[1]["citations"][0]["excerpt"]
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_answer_endpoint_rejects_missing_conversation_before_generation(
    tmp_path: Path,
) -> None:
    answers = FixedAnswerProvider()
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
        answer_provider=answers,
    )

    with TestClient(application) as client:
        response = client.post(
            "/answers",
            json={"question": "Question", "conversation_id": "missing"},
        )
        missing_delete = client.delete("/conversations/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"
    assert missing_delete.status_code == 404
    assert answers.calls == []


def test_response_feedback_can_be_changed_cleared_and_validated(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
        answer_provider=FixedAnswerProvider(),
    )

    with TestClient(application) as client:
        client.post(
            "/documents",
            files={"file": ("manual.txt", b"Pump isolation procedure.", "text/plain")},
        )
        answer = client.post("/answers", json={"question": "How do I isolate it?"})
        conversation_id = answer.json()["conversation_id"]
        initial = client.get(f"/conversations/{conversation_id}").json()
        user_message, assistant_message = initial["messages"]
        helpful = client.put(
            f"/conversations/{conversation_id}/messages/{assistant_message['id']}/feedback",
            json={"rating": "up"},
        )
        changed = client.put(
            f"/conversations/{conversation_id}/messages/{assistant_message['id']}/feedback",
            json={"rating": "down"},
        )
        rated = client.get(f"/conversations/{conversation_id}")
        invalid_target = client.put(
            f"/conversations/{conversation_id}/messages/{user_message['id']}/feedback",
            json={"rating": "up"},
        )
        missing = client.put(
            f"/conversations/{conversation_id}/messages/missing/feedback",
            json={"rating": "up"},
        )
        invalid_rating = client.put(
            f"/conversations/{conversation_id}/messages/{assistant_message['id']}/feedback",
            json={"rating": "maybe"},
        )
        cleared = client.delete(
            f"/conversations/{conversation_id}/messages/{assistant_message['id']}/feedback"
        )
        after_clear = client.get(f"/conversations/{conversation_id}")

    assert helpful.status_code == 200
    assert helpful.json()["rating"] == "up"
    assert changed.json()["rating"] == "down"
    assert rated.json()["messages"][1]["feedback"] == "down"
    assert invalid_target.status_code == 422
    assert invalid_target.json()["error"]["code"] == "feedback_not_allowed"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "response_not_found"
    assert invalid_rating.status_code == 422
    assert cleared.status_code == 204
    assert after_clear.json()["messages"][1]["feedback"] is None


def test_answer_endpoint_requires_both_providers(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
        answer_provider=None,
    )

    with TestClient(application) as client:
        response = client.post("/answers", json={"question": "How do I isolate it?"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "answers_disabled"


def test_answer_endpoint_translates_unverifiable_provider_output(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
        answer_provider=FixedAnswerProvider(
            answer="Unsupported statement [S9].",
            citation_ids=("S9",),
        ),
    )

    with TestClient(application) as client:
        client.post(
            "/documents",
            files={"file": ("manual.txt", b"Pump isolation procedure.", "text/plain")},
        )
        response = client.post("/answers", json={"question": "How do I isolate it?"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_answer_response"


def test_answer_request_rejects_blank_question(tmp_path: Path) -> None:
    application = create_app(
        settings=_settings(tmp_path),
        embedding_provider=KeywordEmbeddingProvider(),
        answer_provider=FixedAnswerProvider(),
    )

    with TestClient(application) as client:
        response = client.post("/answers", json={"question": "   "})

    assert response.status_code == 422


@pytest.mark.parametrize("filename", [None, "", ".", "../.."])
def test_safe_filename_requires_a_real_name(filename: str | None) -> None:
    with pytest.raises(ApiError) as captured:
        _safe_filename(filename)

    assert captured.value.code == "missing_filename"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (IngestionErrorCode.FILE_NOT_FOUND, 404),
        (IngestionErrorCode.UNSUPPORTED_TYPE, 415),
        (IngestionErrorCode.FILE_TOO_LARGE, 413),
        (IngestionErrorCode.OCR_UNAVAILABLE, 503),
        (IngestionErrorCode.OCR_TIMED_OUT, 504),
        (IngestionErrorCode.OCR_FAILED, 422),
        (IngestionErrorCode.VISUAL_ANALYSIS_UNAVAILABLE, 503),
        (IngestionErrorCode.VISUAL_ANALYSIS_TIMED_OUT, 504),
        (IngestionErrorCode.VISUAL_ANALYSIS_FAILED, 502),
        (IngestionErrorCode.EMBEDDING_FAILED, 502),
        (IngestionErrorCode.STORAGE_FAILED, 500),
        (IngestionErrorCode.INVALID_DOCUMENT, 422),
    ],
)
def test_ingestion_errors_map_to_http_statuses(
    code: IngestionErrorCode,
    expected: int,
) -> None:
    assert _ingestion_status(code) == expected


def test_api_command_runs_uvicorn_with_local_defaults() -> None:
    with patch("maintenance_assistant.api.__main__.uvicorn.run") as run:
        exit_code = main([])

    assert exit_code == 0
    run.assert_called_once_with(
        "maintenance_assistant.api.app:app",
        host="127.0.0.1",
        port=8000,
    )
