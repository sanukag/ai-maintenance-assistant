from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openai import OpenAIError
import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import (
    ChunkLocation,
    DocumentFormat,
    StoredChunk,
    StoredDocument,
    VectorSearchResult,
)
from maintenance_assistant.reranking import (
    OpenAIReranker,
    RerankingError,
    create_reranker,
)


def _candidate(chunk_id: str = "chunk-1") -> VectorSearchResult:
    document = StoredDocument(
        id="document-1",
        content_hash="hash",
        original_filename="pump.pdf",
        stored_path=Path("managed/pump.pdf"),
        format=DocumentFormat.PDF,
        size_bytes=100,
        title="Pump manual",
        page_count=3,
        chunk_count=1,
        extractor_name="pypdf",
        extractor_version="1",
        created_at=datetime.now(UTC),
    )
    chunk = StoredChunk(
        id=chunk_id,
        document_id=document.id,
        sequence=0,
        text="Isolate the pump before removing its guard.",
        character_count=45,
        location=ChunkLocation(page_start=2, page_end=2),
    )
    return VectorSearchResult(0.5, "embedding-test", chunk, document)


def test_openai_reranker_uses_bounded_structured_output() -> None:
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=SimpleNamespace(
            results=[SimpleNamespace(chunk_id="chunk-1", score=0.91)]
        )
    )
    provider = OpenAIReranker(
        api_key="test-key",
        model="gpt-rerank-test",
        max_output_tokens=500,
        client=client,
    )

    scores = provider.rerank("How do I remove the guard?", [_candidate()])

    assert scores[0].chunk_id == "chunk-1"
    assert scores[0].score == pytest.approx(0.91)
    arguments = client.responses.parse.call_args.kwargs
    assert arguments["model"] == "gpt-rerank-test"
    assert arguments["max_output_tokens"] == 500
    assert arguments["store"] is False
    assert arguments["text_format"].__name__ == "_RerankPayload"
    assert "untrusted evidence" in arguments["instructions"]
    assert "Isolate the pump" in arguments["input"]


@pytest.mark.parametrize(
    "results",
    [
        [],
        [SimpleNamespace(chunk_id="other", score=0.5)],
        [
            SimpleNamespace(chunk_id="chunk-1", score=0.5),
            SimpleNamespace(chunk_id="chunk-1", score=0.4),
        ],
    ],
)
def test_openai_reranker_rejects_incomplete_or_duplicate_scores(results: list[object]) -> None:
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=SimpleNamespace(results=results)
    )

    with pytest.raises(RerankingError):
        OpenAIReranker(api_key="test-key", client=client).rerank(
            "pump", [_candidate()]
        )


def test_openai_reranker_wraps_provider_failures_and_missing_output() -> None:
    client = Mock()
    client.responses.parse.side_effect = OpenAIError("unavailable")
    with pytest.raises(RerankingError):
        OpenAIReranker(api_key="test-key", client=client).rerank(
            "pump", [_candidate()]
        )

    client.responses.parse.side_effect = None
    client.responses.parse.return_value = SimpleNamespace(output_parsed=None)
    with pytest.raises(RerankingError):
        OpenAIReranker(api_key="test-key", client=client).rerank(
            "pump", [_candidate()]
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"api_key": ""},
        {"api_key": "key", "model": " "},
        {"api_key": "key", "max_output_tokens": 0},
    ],
)
def test_openai_reranker_rejects_invalid_initialisation(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OpenAIReranker(**arguments)


def test_reranker_factory_supports_disabled_and_openai_modes() -> None:
    assert create_reranker(Settings()) is None
    provider = create_reranker(
        Settings(rerank_provider="openai", openai_api_key="test-key")
    )
    assert isinstance(provider, OpenAIReranker)

