from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import OpenAIError

from maintenance_assistant.config import Settings
from maintenance_assistant.embeddings import (
    CachingEmbeddingProvider,
    EmbeddingBatch,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)
from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode


def _response(*vectors: list[float], tokens: int = 12):
    return SimpleNamespace(
        data=[
            SimpleNamespace(index=index, embedding=vector)
            for index, vector in reversed(list(enumerate(vectors)))
        ],
        usage=SimpleNamespace(total_tokens=tokens),
    )


def test_openai_provider_batches_inputs_and_preserves_order() -> None:
    client = Mock()
    client.embeddings.create.side_effect = [
        _response([1.0, 0.0], [0.0, 1.0], tokens=5),
        _response([0.5, 0.5], tokens=3),
    ]
    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        dimensions=2,
        batch_size=2,
        client=client,
    )

    result = provider.embed(["pump", "valve", "motor"])

    assert result.vectors == ((1.0, 0.0), (0.0, 1.0), (0.5, 0.5))
    assert result.input_tokens == 8
    assert client.embeddings.create.call_count == 2
    client.embeddings.create.assert_any_call(
        input=["pump", "valve"],
        model="text-embedding-3-small",
        dimensions=2,
        encoding_format="float",
    )


@pytest.mark.parametrize("texts", [[], [""], ["valid", "   "]])
def test_openai_provider_rejects_empty_input(texts: list[str]) -> None:
    provider = OpenAIEmbeddingProvider(api_key="test-key", client=Mock())

    with pytest.raises(ValueError):
        provider.embed(texts)


def test_openai_provider_rejects_wrong_vector_size() -> None:
    client = Mock()
    client.embeddings.create.return_value = _response([1.0])
    provider = OpenAIEmbeddingProvider(
        api_key="test-key", dimensions=2, client=client
    )

    with pytest.raises(IngestionError) as captured:
        provider.embed(["pump"])

    assert captured.value.code is IngestionErrorCode.EMBEDDING_FAILED


def test_openai_provider_wraps_sdk_failure() -> None:
    client = Mock()
    client.embeddings.create.side_effect = OpenAIError("service unavailable")
    provider = OpenAIEmbeddingProvider(api_key="test-key", client=client)

    with pytest.raises(IngestionError) as captured:
        provider.embed(["pump"])

    assert captured.value.code is IngestionErrorCode.EMBEDDING_FAILED
    assert "OpenAI" in captured.value.message


def test_openai_provider_rejects_non_finite_vector() -> None:
    client = Mock()
    client.embeddings.create.return_value = _response([float("nan"), 1.0])
    provider = OpenAIEmbeddingProvider(
        api_key="test-key", dimensions=2, client=client
    )

    with pytest.raises(IngestionError) as captured:
        provider.embed(["pump"])

    assert captured.value.code is IngestionErrorCode.EMBEDDING_FAILED


def test_openai_provider_rejects_incomplete_indices() -> None:
    client = Mock()
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=1, embedding=[1.0, 0.0])],
        usage=SimpleNamespace(total_tokens=1),
    )
    provider = OpenAIEmbeddingProvider(
        api_key="test-key", dimensions=2, client=client
    )

    with pytest.raises(IngestionError) as captured:
        provider.embed(["pump"])

    assert captured.value.code is IngestionErrorCode.EMBEDDING_FAILED


@pytest.mark.parametrize(
    "arguments",
    [
        {"api_key": ""},
        {"api_key": "key", "model": " "},
        {"api_key": "key", "dimensions": 0},
        {"api_key": "key", "batch_size": 2049},
    ],
)
def test_openai_provider_rejects_invalid_initialisation(arguments: dict) -> None:
    with pytest.raises(ValueError):
        OpenAIEmbeddingProvider(**arguments)


def test_provider_factory_preserves_local_only_mode() -> None:
    assert create_embedding_provider(Settings()) is None


def test_provider_factory_builds_openai_provider() -> None:
    provider = create_embedding_provider(
        Settings(embedding_provider="openai", openai_api_key="test-key")
    )

    assert isinstance(provider, OpenAIEmbeddingProvider)


def test_caching_provider_reuses_deduplicated_vectors_in_input_order() -> None:
    class Provider:
        model = "test-model"
        dimensions = 2

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def embed(self, texts):
            values = tuple(texts)
            self.calls.append(values)
            return EmbeddingBatch(
                self.model,
                self.dimensions,
                tuple((float(len(text)), 1.0) for text in values),
                7,
            )

    class Cache:
        def __init__(self) -> None:
            self.values: dict[str, tuple[float, ...]] = {}

        def get_cached_embeddings(self, keys, **_kwargs):
            return {key: self.values[key] for key in keys if key in self.values}

        def put_cached_embeddings(self, entries, **_kwargs):
            self.values.update(entries)

    raw = Provider()
    provider = CachingEmbeddingProvider(raw, Cache(), max_entries=5)

    first = provider.embed(["pump", "pump", "valve"])
    second = provider.embed(["valve", "pump"])

    assert raw.calls == [("pump", "valve")]
    assert first.vectors == ((4.0, 1.0), (4.0, 1.0), (5.0, 1.0))
    assert first.input_tokens == 7
    assert second.vectors == ((5.0, 1.0), (4.0, 1.0))
    assert second.input_tokens == 0


@pytest.mark.parametrize("texts", [[], [""], ["valid", " "]])
def test_caching_provider_rejects_empty_inputs(texts: list[str]) -> None:
    raw = Mock(model="test", dimensions=2)
    cache = Mock()
    provider = CachingEmbeddingProvider(raw, cache)

    with pytest.raises(ValueError):
        provider.embed(texts)


def test_caching_provider_requires_a_positive_bound() -> None:
    with pytest.raises(ValueError):
        CachingEmbeddingProvider(Mock(model="test", dimensions=2), Mock(), max_entries=0)
