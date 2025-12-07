"""Environment-based application configuration."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Mapping

DEFAULT_FILE_TYPES = (".pdf", ".txt", ".md")
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings shared by local application components."""

    data_directory: Path = Path("data")
    max_document_size_mb: int = 25
    supported_file_types: tuple[str, ...] = DEFAULT_FILE_TYPES
    chunk_size_characters: int = 2400
    chunk_overlap_characters: int = 400
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
            values.get("AMA_CHUNK_SIZE_CHARACTERS", "2400"),
            "AMA_CHUNK_SIZE_CHARACTERS",
        )
        chunk_overlap = _non_negative_integer(
            values.get("AMA_CHUNK_OVERLAP_CHARACTERS", "400"),
            "AMA_CHUNK_OVERLAP_CHARACTERS",
        )
        if chunk_overlap >= chunk_size:
            raise ValueError(
                "AMA_CHUNK_OVERLAP_CHARACTERS must be smaller than "
                "AMA_CHUNK_SIZE_CHARACTERS"
            )
        log_level = values.get("AMA_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"AMA_LOG_LEVEL must be one of: {allowed}")

        return cls(
            data_directory=Path(values.get("AMA_DATA_DIRECTORY", "data")).expanduser(),
            max_document_size_mb=max_size,
            supported_file_types=file_types,
            chunk_size_characters=chunk_size,
            chunk_overlap_characters=chunk_overlap,
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
