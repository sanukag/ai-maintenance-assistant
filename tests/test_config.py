from pathlib import Path

import pytest

from maintenance_assistant.config import DEFAULT_FILE_TYPES, Settings


def test_settings_use_local_defaults() -> None:
    settings = Settings.from_environment({})

    assert settings.data_directory == Path("data")
    assert settings.max_document_size_mb == 25
    assert settings.supported_file_types == DEFAULT_FILE_TYPES
    assert settings.chunk_size_tokens == 300
    assert settings.chunk_overlap_tokens == 40
    assert settings.chunk_token_encoding == "cl100k_base"
    assert settings.embedding_provider == "none"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 512
    assert settings.embedding_batch_size == 128
    assert settings.answer_provider == "none"
    assert settings.answer_model == "gpt-5.6-terra"
    assert settings.answer_max_output_tokens == 1000
    assert settings.openai_api_key is None
    assert settings.log_level == "INFO"


def test_settings_read_environment_values() -> None:
    settings = Settings.from_environment(
        {
            "AMA_DATA_DIRECTORY": "~/maintenance-data",
            "AMA_MAX_DOCUMENT_SIZE_MB": "40",
            "AMA_SUPPORTED_FILE_TYPES": "PDF, .txt, pdf",
            "AMA_CHUNK_SIZE_TOKENS": "240",
            "AMA_CHUNK_OVERLAP_TOKENS": "30",
            "AMA_CHUNK_TOKEN_ENCODING": "cl100k_base",
            "AMA_EMBEDDING_PROVIDER": "openai",
            "AMA_EMBEDDING_MODEL": "text-embedding-3-large",
            "AMA_EMBEDDING_DIMENSIONS": "1024",
            "AMA_EMBEDDING_BATCH_SIZE": "64",
            "AMA_ANSWER_PROVIDER": "openai",
            "AMA_ANSWER_MODEL": "gpt-answer",
            "AMA_ANSWER_MAX_OUTPUT_TOKENS": "750",
            "OPENAI_API_KEY": "test-key",
            "AMA_LOG_LEVEL": "debug",
        }
    )

    assert settings.data_directory == Path("~/maintenance-data").expanduser()
    assert settings.max_document_size_mb == 40
    assert settings.supported_file_types == (".pdf", ".txt")
    assert settings.chunk_size_tokens == 240
    assert settings.chunk_overlap_tokens == 30
    assert settings.chunk_token_encoding == "cl100k_base"
    assert settings.embedding_provider == "openai"
    assert settings.embedding_model == "text-embedding-3-large"
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_batch_size == 64
    assert settings.answer_provider == "openai"
    assert settings.answer_model == "gpt-answer"
    assert settings.answer_max_output_tokens == 750
    assert settings.openai_api_key == "test-key"
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
        {"AMA_CHUNK_SIZE_TOKENS": "0"},
        {"AMA_CHUNK_OVERLAP_TOKENS": "-1"},
        {
            "AMA_CHUNK_SIZE_TOKENS": "100",
            "AMA_CHUNK_OVERLAP_TOKENS": "100",
        },
        {"AMA_CHUNK_TOKEN_ENCODING": " "},
        {"AMA_CHUNK_TOKEN_ENCODING": "unknown"},
    ],
)
def test_settings_reject_invalid_chunk_limits(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="AMA_CHUNK"):
        Settings.from_environment(environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"AMA_EMBEDDING_PROVIDER": "unknown"},
        {"AMA_EMBEDDING_PROVIDER": "openai"},
        {"AMA_EMBEDDING_DIMENSIONS": "0"},
        {"AMA_EMBEDDING_BATCH_SIZE": "2049"},
        {"AMA_EMBEDDING_MODEL": " "},
    ],
)
def test_settings_reject_invalid_embedding_configuration(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        Settings.from_environment(environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"AMA_ANSWER_PROVIDER": "unknown"},
        {"AMA_ANSWER_PROVIDER": "openai"},
        {"AMA_ANSWER_MODEL": " "},
        {"AMA_ANSWER_MAX_OUTPUT_TOKENS": "0"},
    ],
)
def test_settings_reject_invalid_answer_configuration(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        Settings.from_environment(environment)
