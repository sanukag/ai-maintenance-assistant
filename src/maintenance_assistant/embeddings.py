"""Embedding provider contracts and production OpenAI implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping, Protocol

from openai import OpenAI, OpenAIError

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion.errors import IngestionError, IngestionErrorCode


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Ordered vectors returned for one logical embedding request."""

    model: str
    dimensions: int
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int


class EmbeddingProvider(Protocol):
    """The embedding behaviour required by ingestion and retrieval."""

    model: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Return one ordered vector for each supplied text."""


class EmbeddingCache(Protocol):
    """Persistence seam used without coupling providers to SQLite."""

    def get_cached_embeddings(
        self,
        cache_keys: Sequence[str],
        *,
        model: str,
        dimensions: int,
    ) -> Mapping[str, tuple[float, ...]]: ...

    def put_cached_embeddings(
        self,
        entries: Mapping[str, Sequence[float]],
        *,
        model: str,
        dimensions: int,
        max_entries: int,
    ) -> None: ...


class OpenAIEmbeddingProvider:
    """Create batched text embeddings through the OpenAI Embeddings API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 512,
        batch_size: int = 128,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if dimensions < 1:
            raise ValueError("dimensions must be greater than zero")
        if batch_size < 1 or batch_size > 2048:
            raise ValueError("batch_size must be between 1 and 2048")
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._client = client or OpenAI(api_key=api_key)

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Embed non-empty text in bounded batches and preserve input order."""

        inputs = tuple(texts)
        if not inputs:
            raise ValueError("at least one text value is required")
        if any(not text.strip() for text in inputs):
            raise ValueError("embedding input must not be empty")

        vectors: list[tuple[float, ...]] = []
        input_tokens = 0
        try:
            for start in range(0, len(inputs), self.batch_size):
                batch = inputs[start : start + self.batch_size]
                response = self._client.embeddings.create(
                    input=list(batch),
                    model=self.model,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in ordered] != list(range(len(batch))):
                    raise IngestionError(
                        IngestionErrorCode.EMBEDDING_FAILED,
                        "Embedding provider returned incomplete or unordered results",
                    )
                vectors.extend(tuple(float(value) for value in item.embedding) for item in ordered)
                input_tokens += int(response.usage.total_tokens)
        except IngestionError:
            raise
        except OpenAIError as error:
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_FAILED,
                "OpenAI could not create document embeddings",
            ) from error

        if any(len(vector) != self.dimensions for vector in vectors):
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_FAILED,
                "Embedding provider returned an unexpected vector size",
            )
        if any(not all(isfinite(value) for value in vector) for vector in vectors):
            raise IngestionError(
                IngestionErrorCode.EMBEDDING_FAILED,
                "Embedding provider returned a non-finite vector value",
            )
        return EmbeddingBatch(
            model=self.model,
            dimensions=self.dimensions,
            vectors=tuple(vectors),
            input_tokens=input_tokens,
        )


class CachingEmbeddingProvider:
    """Reuse identical model inputs across ingestion, search and restarts."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        cache: EmbeddingCache,
        *,
        max_entries: int = 10_000,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be greater than zero")
        self.provider = provider
        self.cache = cache
        self.max_entries = max_entries
        self.model = provider.model
        self.dimensions = provider.dimensions

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        inputs = tuple(texts)
        if not inputs:
            raise ValueError("at least one text value is required")
        if any(not text.strip() for text in inputs):
            raise ValueError("embedding input must not be empty")
        keys = tuple(self._key(text) for text in inputs)
        cached = dict(
            self.cache.get_cached_embeddings(
                keys,
                model=self.model,
                dimensions=self.dimensions,
            )
        )
        missing: dict[str, str] = {}
        for key, text in zip(keys, inputs, strict=True):
            if key not in cached:
                missing.setdefault(key, text)
        input_tokens = 0
        if missing:
            batch = self.provider.embed(tuple(missing.values()))
            if len(batch.vectors) != len(missing):
                raise IngestionError(
                    IngestionErrorCode.EMBEDDING_FAILED,
                    "Embedding provider returned an unexpected vector count",
                )
            generated = dict(zip(missing, batch.vectors, strict=True))
            self.cache.put_cached_embeddings(
                generated,
                model=self.model,
                dimensions=self.dimensions,
                max_entries=self.max_entries,
            )
            cached.update(generated)
            input_tokens = batch.input_tokens
        return EmbeddingBatch(
            model=self.model,
            dimensions=self.dimensions,
            vectors=tuple(cached[key] for key in keys),
            input_tokens=input_tokens,
        )

    def _key(self, text: str) -> str:
        value = f"{self.model}\0{self.dimensions}\0{text}".encode("utf-8")
        return sha256(value).hexdigest()


def create_embedding_provider(
    settings: Settings,
    cache: EmbeddingCache | None = None,
) -> EmbeddingProvider | None:
    """Create the configured production provider, or preserve local-only mode."""

    if settings.embedding_provider == "none":
        return None
    if settings.embedding_provider == "openai" and settings.openai_api_key:
        provider: EmbeddingProvider = OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
        )
        if cache is not None and settings.embedding_cache_max_entries > 0:
            return CachingEmbeddingProvider(
                provider,
                cache,
                max_entries=settings.embedding_cache_max_entries,
            )
        return provider
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
