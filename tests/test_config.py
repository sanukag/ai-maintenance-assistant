from pathlib import Path

import pytest

from maintenance_assistant.config import DEFAULT_FILE_TYPES, Settings


def test_settings_use_local_defaults() -> None:
    settings = Settings.from_environment({})

    assert settings.data_directory == Path("data")
    assert settings.max_document_size_mb == 25
    assert settings.supported_file_types == DEFAULT_FILE_TYPES
    assert settings.chunk_size_characters == 2400
    assert settings.chunk_overlap_characters == 400
    assert settings.log_level == "INFO"


def test_settings_read_environment_values() -> None:
    settings = Settings.from_environment(
        {
            "AMA_DATA_DIRECTORY": "~/maintenance-data",
            "AMA_MAX_DOCUMENT_SIZE_MB": "40",
            "AMA_SUPPORTED_FILE_TYPES": "PDF, .txt, pdf",
            "AMA_CHUNK_SIZE_CHARACTERS": "1200",
            "AMA_CHUNK_OVERLAP_CHARACTERS": "200",
            "AMA_LOG_LEVEL": "debug",
        }
    )

    assert settings.data_directory == Path("~/maintenance-data").expanduser()
    assert settings.max_document_size_mb == 40
    assert settings.supported_file_types == (".pdf", ".txt")
    assert settings.chunk_size_characters == 1200
    assert settings.chunk_overlap_characters == 200
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["0", "-1", "large"])
def test_settings_reject_invalid_document_size(value: str) -> None:
    with pytest.raises(ValueError, match="AMA_MAX_DOCUMENT_SIZE_MB"):
        Settings.from_environment({"AMA_MAX_DOCUMENT_SIZE_MB": value})


def test_settings_reject_unknown_log_level() -> None:
    with pytest.raises(ValueError, match="AMA_LOG_LEVEL"):
        Settings.from_environment({"AMA_LOG_LEVEL": "verbose"})


@pytest.mark.parametrize(
    "environment",
    [
        {"AMA_CHUNK_SIZE_CHARACTERS": "0"},
        {"AMA_CHUNK_OVERLAP_CHARACTERS": "-1"},
        {
            "AMA_CHUNK_SIZE_CHARACTERS": "100",
            "AMA_CHUNK_OVERLAP_CHARACTERS": "100",
        },
    ],
)
def test_settings_reject_invalid_chunk_limits(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="AMA_CHUNK"):
        Settings.from_environment(environment)
