"""Indexed dense-vector storage with Qdrant and a SQLite source of truth."""

from __future__ import annotations

from hashlib import sha256
import json
import logging
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion.models import DocumentLifecycleStatus, DocumentMetadata
from maintenance_assistant.ingestion.storage import LocalDocumentStore

logger = logging.getLogger(__name__)


class VectorIndexError(RuntimeError):
    """The external vector index could not complete an operation."""


class QdrantVectorIndex:
    """Synchronise SQLite embeddings into a filterable Qdrant HNSW index."""

    def __init__(
        self,
        store: LocalDocumentStore,
        *,
        url: str,
        model: str,
        dimensions: int,
        timeout_seconds: int = 5,
    ) -> None:
        self.store = store
        self.url = url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        digest = sha256(f"{model}:{dimensions}".encode()).hexdigest()[:12]
        self.collection = f"maintenance_chunks_{digest}"

    def available(self) -> bool:
        try:
            self._request("GET", "/healthz")
            return True
        except VectorIndexError:
            return False

    def ensure_collection(self) -> None:
        try:
            self._request("GET", f"/collections/{self.collection}")
            return
        except VectorIndexError as error:
            if "HTTP 404" not in str(error):
                raise
        self._request(
            "PUT",
            f"/collections/{self.collection}",
            {"vectors": {"size": self.dimensions, "distance": "Cosine"}, "on_disk_payload": True},
        )
        for field in ("document_id", "lifecycle_status", "model", "brand", "machine", "site", "document_type"):
            self._request(
                "PUT",
                f"/collections/{self.collection}/index?wait=true",
                {"field_name": field, "field_schema": "keyword"},
            )

    def index_document(self, document_id: str) -> int:
        self.ensure_collection()
        self.remove_document(document_id, ensure=False)
        document = self.store.get_document(document_id)
        if document is None or document.lifecycle_status is not DocumentLifecycleStatus.CURRENT:
            return 0
        chunks = {chunk.id: chunk for chunk in self.store.list_chunks(document_id)}
        embeddings = self.store.list_embeddings(
            document_id,
            model=self.model,
            dimensions=self.dimensions,
        )
        points = []
        for embedding in embeddings:
            chunk = chunks.get(embedding.chunk_id)
            if chunk is None:
                continue
            points.append({
                "id": chunk.id,
                "vector": list(embedding.vector),
                "payload": {
                    "document_id": document.id,
                    "lifecycle_status": document.lifecycle_status.value,
                    "model": self.model,
                    "dimensions": self.dimensions,
                    "brand": list(document.metadata.brand),
                    "machine": list(document.metadata.machine),
                    "site": list(document.metadata.site),
                    "document_type": list(document.metadata.document_type),
                },
            })
        for start in range(0, len(points), 128):
            self._request(
                "PUT",
                f"/collections/{self.collection}/points?wait=true",
                {"points": points[start : start + 128]},
            )
        return len(points)

    def remove_document(self, document_id: str, *, ensure: bool = True) -> None:
        if ensure:
            self.ensure_collection()
        self._request(
            "POST",
            f"/collections/{self.collection}/points/delete?wait=true",
            {"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}},
        )

    def search(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        document_id: str | None,
        metadata: DocumentMetadata,
    ) -> tuple[tuple[str, float], ...]:
        self.ensure_collection()
        must: list[dict[str, Any]] = [
            {"key": "lifecycle_status", "match": {"value": "current"}},
            {"key": "model", "match": {"value": self.model}},
            {"key": "dimensions", "match": {"value": self.dimensions}},
        ]
        if document_id is not None:
            must.append({"key": "document_id", "match": {"value": document_id}})
        for key in ("brand", "machine", "site", "document_type"):
            values = getattr(metadata, key)
            if values:
                must.append({"key": key, "match": {"any": list(values)}})
        response = self._request(
            "POST",
            f"/collections/{self.collection}/points/query",
            {"query": list(vector), "filter": {"must": must}, "limit": limit, "with_payload": False},
        )
        result = response.get("result", {})
        points = result.get("points", result if isinstance(result, list) else [])
        return tuple((str(point["id"]), float(point["score"])) for point in points)

    def rebuild(self) -> int:
        try:
            self._request("DELETE", f"/collections/{self.collection}")
        except VectorIndexError as error:
            if "HTTP 404" not in str(error):
                raise
        self.ensure_collection()
        indexed = 0
        for document in self.store.list_documents(limit=100_000, lifecycle_status=DocumentLifecycleStatus.CURRENT):
            indexed += self.index_document(document.id)
        return indexed

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except HTTPError as error:
            raise VectorIndexError(f"Qdrant returned HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise VectorIndexError("Qdrant is unavailable") from error
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}


def create_vector_index(settings: Settings, store: LocalDocumentStore) -> QdrantVectorIndex | None:
    if settings.vector_store == "sqlite":
        return None
    return QdrantVectorIndex(
        store,
        url=settings.qdrant_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.qdrant_timeout_seconds,
    )
