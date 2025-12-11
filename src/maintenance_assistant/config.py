"""Environment-based application configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from os import environ
from pathlib import Path
from typing import Mapping

DEFAULT_FILE_TYPES = (".pdf", ".txt", ".md")
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
VALID_EMBEDDING_PROVIDERS = frozenset({"none", "openai"})
VALID_ANSWER_PROVIDERS = frozenset({"none", "openai"})
VALID_TOKEN_ENCODINGS = frozenset({"cl100k_base"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings shared by local application components."""

    data_directory: Path = Path("data")
    max_document_size_mb: int = 25
    supported_file_types: tuple[str, ...] = DEFAULT_FILE_TYPES
    chunk_size_tokens: int = 300
    chunk_overlap_tokens: int = 40
    parent_chunk_size_tokens: int = 900
    chunk_token_encoding: str = "cl100k_base"
    embedding_provider: str = "none"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 512
    embedding_batch_size: int = 128
    retrieval_candidate_limit: int = 30
    retrieval_rrf_k: int = 60
    retrieval_semantic_weight: float = 1.0
    retrieval_text_weight: float = 1.0
    answer_provider: str = "none"
    answer_model: str = "gpt-5.6-terra"
    answer_max_output_tokens: int = 1_000
    openai_api_key: str | None = field(default=None, repr=False)
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Settings:
        """Build settings from an environment mapping or the process environment."""

        values = environ if environment is None else environment
        max_size = _positive_integer(
            values.get("AMA_MAX_DOCUMENT_SIZE_MB", "25"),
            "AMA_MAX_DOCUMENT_SIZE_MB",
        )
        file_types = _file_types(values.get("AMA_SUPPORTED_FILE_TYPES", ""))
        chunk_size = _positive_integer(
            values.get("AMA_CHUNK_SIZE_TOKENS", "300"),
            "AMA_CHUNK_SIZE_TOKENS",
        )
        chunk_overlap = _non_negative_integer(
            values.get("AMA_CHUNK_OVERLAP_TOKENS", "40"),
            "AMA_CHUNK_OVERLAP_TOKENS",
        )
        if chunk_overlap >= chunk_size:
            raise ValueError(
                "AMA_CHUNK_OVERLAP_TOKENS must be smaller than "
                "AMA_CHUNK_SIZE_TOKENS"
            )
        parent_chunk_size = _positive_integer(
            values.get("AMA_PARENT_CHUNK_SIZE_TOKENS", "900"),
            "AMA_PARENT_CHUNK_SIZE_TOKENS",
        )
        if parent_chunk_size < chunk_size:
            raise ValueError(
                "AMA_PARENT_CHUNK_SIZE_TOKENS must not be smaller than "
                "AMA_CHUNK_SIZE_TOKENS"
            )
        chunk_token_encoding = values.get(
            "AMA_CHUNK_TOKEN_ENCODING", "cl100k_base"
        ).strip()
        if not chunk_token_encoding:
            raise ValueError("AMA_CHUNK_TOKEN_ENCODING must not be empty")
        if chunk_token_encoding not in VALID_TOKEN_ENCODINGS:
            allowed = ", ".join(sorted(VALID_TOKEN_ENCODINGS))
            raise ValueError(f"AMA_CHUNK_TOKEN_ENCODING must be one of: {allowed}")
        embedding_provider = values.get("AMA_EMBEDDING_PROVIDER", "none").strip().lower()
        if embedding_provider not in VALID_EMBEDDING_PROVIDERS:
            allowed = ", ".join(sorted(VALID_EMBEDDING_PROVIDERS))
            raise ValueError(f"AMA_EMBEDDING_PROVIDER must be one of: {allowed}")
        embedding_model = values.get(
            "AMA_EMBEDDING_MODEL", "text-embedding-3-small"
        ).strip()
        if not embedding_model:
            raise ValueError("AMA_EMBEDDING_MODEL must not be empty")
        embedding_dimensions = _positive_integer(
            values.get("AMA_EMBEDDING_DIMENSIONS", "512"),
            "AMA_EMBEDDING_DIMENSIONS",
        )
        embedding_batch_size = _positive_integer(
            values.get("AMA_EMBEDDING_BATCH_SIZE", "128"),
            "AMA_EMBEDDING_BATCH_SIZE",
        )
        if embedding_batch_size > 2048:
            raise ValueError("AMA_EMBEDDING_BATCH_SIZE must not exceed 2048")
        retrieval_candidate_limit = _positive_integer(
            values.get("AMA_RETRIEVAL_CANDIDATE_LIMIT", "30"),
            "AMA_RETRIEVAL_CANDIDATE_LIMIT",
        )
        retrieval_rrf_k = _positive_integer(
            values.get("AMA_RETRIEVAL_RRF_K", "60"),
            "AMA_RETRIEVAL_RRF_K",
        )
        retrieval_semantic_weight = _non_negative_float(
            values.get("AMA_RETRIEVAL_SEMANTIC_WEIGHT", "1"),
            "AMA_RETRIEVAL_SEMANTIC_WEIGHT",
        )
        retrieval_text_weight = _non_negative_float(
            values.get("AMA_RETRIEVAL_TEXT_WEIGHT", "1"),
            "AMA_RETRIEVAL_TEXT_WEIGHT",
        )
        if retrieval_semantic_weight == retrieval_text_weight == 0:
            raise ValueError("At least one retrieval weight must be greater than zero")
        answer_provider = values.get("AMA_ANSWER_PROVIDER", "none").strip().lower()
        if answer_provider not in VALID_ANSWER_PROVIDERS:
            allowed = ", ".join(sorted(VALID_ANSWER_PROVIDERS))
            raise ValueError(f"AMA_ANSWER_PROVIDER must be one of: {allowed}")
        answer_model = values.get("AMA_ANSWER_MODEL", "gpt-5.6-terra").strip()
        if not answer_model:
            raise ValueError("AMA_ANSWER_MODEL must not be empty")
        answer_max_output_tokens = _positive_integer(
            values.get("AMA_ANSWER_MAX_OUTPUT_TOKENS", "1000"),
            "AMA_ANSWER_MAX_OUTPUT_TOKENS",
        )
        openai_api_key = values.get("OPENAI_API_KEY", "").strip() or None
        if (
            embedding_provider == "openai" or answer_provider == "openai"
        ) and openai_api_key is None:
            raise ValueError(
                "OPENAI_API_KEY is required when an OpenAI provider is enabled"
            )
        log_level = values.get("AMA_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"AMA_LOG_LEVEL must be one of: {allowed}")

        return cls(
            data_directory=Path(values.get("AMA_DATA_DIRECTORY", "data")).expanduser(),
            max_document_size_mb=max_size,
            supported_file_types=file_types,
            chunk_size_tokens=chunk_size,
            chunk_overlap_tokens=chunk_overlap,
            parent_chunk_size_tokens=parent_chunk_size,
            chunk_token_encoding=chunk_token_encoding,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            embedding_batch_size=embedding_batch_size,
            retrieval_candidate_limit=retrieval_candidate_limit,
            retrieval_rrf_k=retrieval_rrf_k,
            retrieval_semantic_weight=retrieval_semantic_weight,
            retrieval_text_weight=retrieval_text_weight,
            answer_provider=answer_provider,
            answer_model=answer_model,
            answer_max_output_tokens=answer_max_output_tokens,
            openai_api_key=openai_api_key,
            log_level=log_level,
        )


def _positive_integer(value: str, setting_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{setting_name} must be a whole number") from error
    if parsed < 1:
        raise ValueError(f"{setting_name} must be greater than zero")
    return parsed


def _non_negative_integer(value: str, setting_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{setting_name} must be a whole number") from error
    if parsed < 0:
        raise ValueError(f"{setting_name} must be zero or greater")
    return parsed


def _non_negative_float(value: str, setting_name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{setting_name} must be a number") from error
    if not isfinite(parsed) or parsed < 0:
        raise ValueError(f"{setting_name} must be zero or greater")
    return parsed


def _file_types(value: str) -> tuple[str, ...]:
    if not value.strip():
        return DEFAULT_FILE_TYPES

    file_types = tuple(
        item if item.startswith(".") else f".{item}"
        for part in value.split(",")
        if (item := part.strip().lower())
    )
    if not file_types:
        raise ValueError("AMA_SUPPORTED_FILE_TYPES must contain at least one file type")
    return tuple(dict.fromkeys(file_types))
