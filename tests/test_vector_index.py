"""Qdrant request contracts and SQLite synchronisation."""

from pathlib import Path
from types import MethodType

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import DocumentMetadata, IngestionService, LocalDocumentStore
from maintenance_assistant.vector_index import QdrantVectorIndex
from tests.fakes import KeywordEmbeddingProvider


def test_qdrant_indexes_vectors_payloads_filters_and_removals(tmp_path: Path) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Pump seal replacement.", encoding="utf-8")
    settings = Settings(data_directory=tmp_path / "data")
    provider = KeywordEmbeddingProvider()
    document = IngestionService(settings, embedding_provider=provider).ingest(
        path,
        DocumentMetadata(brand=["Acme", "Northwind"], machine="P-100"),
    ).document
    store = LocalDocumentStore(settings.data_directory)
    index = QdrantVectorIndex(
        store,
        url="http://qdrant:6333",
        model=provider.model,
        dimensions=provider.dimensions,
    )
    calls: list[tuple[str, str, dict | None]] = []

    def request(_self, method: str, route: str, body: dict | None = None):
        calls.append((method, route, body))
        if route.endswith("/points/query"):
            return {"result": {"points": [{"id": store.list_chunks(document.id)[0].id, "score": 0.94}]}}
        return {"status": "ok", "result": True}

    index.ensure_collection = MethodType(lambda _self: None, index)  # type: ignore[method-assign]
    index._request = MethodType(request, index)  # type: ignore[method-assign]

    assert index.index_document(document.id) == 1
    results = index.search(
        (1.0, 0.0, 0.0),
        limit=5,
        document_id=document.id,
        metadata=DocumentMetadata(brand=["Acme", "Northwind"], machine="P-100"),
    )
    index.remove_document(document.id)

    upsert = next(body for method, route, body in calls if method == "PUT" and "/points?" in route)
    assert upsert is not None
    assert upsert["points"][0]["payload"]["brand"] == ["Acme", "Northwind"]
    query = next(body for method, route, body in calls if route.endswith("/points/query"))
    assert query is not None
    assert {condition["key"] for condition in query["filter"]["must"]} >= {
        "document_id", "brand", "machine", "lifecycle_status"
    }
    assert results[0][1] == 0.94
    assert any(method == "POST" and "/points/delete" in route for method, route, _body in calls)
