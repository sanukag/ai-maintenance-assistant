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
    assert settings.parent_chunk_size_tokens == 900
    assert settings.chunk_token_encoding == "cl100k_base"
    assert settings.ocr_provider == "tesseract"
    assert settings.ocr_language == "eng"
    assert settings.ocr_dpi == 300
    assert settings.ocr_page_timeout_seconds == 30
    assert settings.ocr_max_pages == 100
    assert settings.ocr_max_image_pixels == 50_000_000
    assert settings.visual_analysis_provider == "none"
    assert settings.visual_analysis_model == "gpt-5.6-terra"
    assert settings.visual_analysis_detail == "high"
    assert settings.visual_analysis_render_dpi == 150
    assert settings.visual_analysis_timeout_seconds == 60
    assert settings.visual_analysis_max_pages == 100
    assert settings.visual_analysis_max_image_pixels == 25_000_000
    assert settings.visual_analysis_max_output_tokens == 1_000
    assert settings.embedding_provider == "none"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 512
    assert settings.embedding_batch_size == 128
    assert settings.vector_store == "sqlite"
    assert settings.qdrant_url == "http://127.0.0.1:6333"
    assert settings.qdrant_timeout_seconds == 5
    assert settings.retrieval_candidate_limit == 30
    assert settings.retrieval_rrf_k == 60
    assert settings.retrieval_semantic_weight == 1.0
    assert settings.retrieval_text_weight == 1.0
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
            "AMA_PARENT_CHUNK_SIZE_TOKENS": "720",
            "AMA_CHUNK_TOKEN_ENCODING": "cl100k_base",
            "AMA_OCR_PROVIDER": "none",
            "AMA_OCR_LANGUAGE": "eng+fra",
            "AMA_OCR_DPI": "240",
            "AMA_OCR_PAGE_TIMEOUT_SECONDS": "45",
            "AMA_OCR_MAX_PAGES": "75",
            "AMA_OCR_MAX_IMAGE_PIXELS": "25000000",
            "AMA_VISUAL_ANALYSIS_PROVIDER": "openai",
            "AMA_VISUAL_ANALYSIS_MODEL": "gpt-vision",
            "AMA_VISUAL_ANALYSIS_DETAIL": "original",
            "AMA_VISUAL_ANALYSIS_RENDER_DPI": "180",
            "AMA_VISUAL_ANALYSIS_TIMEOUT_SECONDS": "90",
            "AMA_VISUAL_ANALYSIS_MAX_PAGES": "60",
            "AMA_VISUAL_ANALYSIS_MAX_IMAGE_PIXELS": "20000000",
            "AMA_VISUAL_ANALYSIS_MAX_OUTPUT_TOKENS": "800",
            "AMA_EMBEDDING_PROVIDER": "openai",
            "AMA_EMBEDDING_MODEL": "text-embedding-3-large",
            "AMA_EMBEDDING_DIMENSIONS": "1024",
            "AMA_EMBEDDING_BATCH_SIZE": "64",
            "AMA_VECTOR_STORE": "qdrant",
            "AMA_QDRANT_URL": "http://qdrant:6333/",
            "AMA_QDRANT_TIMEOUT_SECONDS": "12",
            "AMA_RETRIEVAL_CANDIDATE_LIMIT": "40",
            "AMA_RETRIEVAL_RRF_K": "50",
            "AMA_RETRIEVAL_SEMANTIC_WEIGHT": "1.5",
            "AMA_RETRIEVAL_TEXT_WEIGHT": "0.75",
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
    assert settings.parent_chunk_size_tokens == 720
    assert settings.chunk_token_encoding == "cl100k_base"
    assert settings.ocr_provider == "none"
    assert settings.ocr_language == "eng+fra"
    assert settings.ocr_dpi == 240
    assert settings.ocr_page_timeout_seconds == 45
    assert settings.ocr_max_pages == 75
    assert settings.ocr_max_image_pixels == 25_000_000
    assert settings.visual_analysis_provider == "openai"
    assert settings.visual_analysis_model == "gpt-vision"
    assert settings.visual_analysis_detail == "original"
    assert settings.visual_analysis_render_dpi == 180
    assert settings.visual_analysis_timeout_seconds == 90
    assert settings.visual_analysis_max_pages == 60
    assert settings.visual_analysis_max_image_pixels == 20_000_000
    assert settings.visual_analysis_max_output_tokens == 800
    assert settings.embedding_provider == "openai"
    assert settings.embedding_model == "text-embedding-3-large"
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_batch_size == 64
    assert settings.vector_store == "qdrant"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.qdrant_timeout_seconds == 12
    assert settings.retrieval_candidate_limit == 40
    assert settings.retrieval_rrf_k == 50
    assert settings.retrieval_semantic_weight == 1.5
    assert settings.retrieval_text_weight == 0.75
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
        {"AMA_VECTOR_STORE": "unknown"},
        {"AMA_QDRANT_URL": "qdrant:6333"},
        {"AMA_QDRANT_TIMEOUT_SECONDS": "0"},
        {"AMA_QDRANT_TIMEOUT_SECONDS": "61"},
    ],
)
def test_settings_reject_invalid_vector_index_configuration(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_environment(environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"AMA_CHUNK_SIZE_TOKENS": "0"},
        {"AMA_CHUNK_OVERLAP_TOKENS": "-1"},
        {
            "AMA_CHUNK_SIZE_TOKENS": "100",
            "AMA_CHUNK_OVERLAP_TOKENS": "100",
        },
        {
            "AMA_CHUNK_SIZE_TOKENS": "100",
            "AMA_PARENT_CHUNK_SIZE_TOKENS": "99",
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
        {"AMA_OCR_PROVIDER": "cloud"},
        {"AMA_OCR_LANGUAGE": "eng;rm"},
        {"AMA_OCR_DPI": "149"},
        {"AMA_OCR_DPI": "601"},
        {"AMA_OCR_PAGE_TIMEOUT_SECONDS": "0"},
        {"AMA_OCR_PAGE_TIMEOUT_SECONDS": "301"},
        {"AMA_OCR_MAX_PAGES": "0"},
        {"AMA_OCR_MAX_IMAGE_PIXELS": "0"},
    ],
)
def test_settings_reject_invalid_ocr_configuration(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="AMA_OCR"):
        Settings.from_environment(environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"AMA_VISUAL_ANALYSIS_PROVIDER": "local"},
        {"AMA_VISUAL_ANALYSIS_PROVIDER": "openai"},
        {"AMA_VISUAL_ANALYSIS_MODEL": " "},
        {"AMA_VISUAL_ANALYSIS_DETAIL": "maximum"},
        {"AMA_VISUAL_ANALYSIS_RENDER_DPI": "99"},
        {"AMA_VISUAL_ANALYSIS_RENDER_DPI": "301"},
        {"AMA_VISUAL_ANALYSIS_TIMEOUT_SECONDS": "0"},
        {"AMA_VISUAL_ANALYSIS_TIMEOUT_SECONDS": "601"},
        {"AMA_VISUAL_ANALYSIS_MAX_PAGES": "0"},
        {"AMA_VISUAL_ANALYSIS_MAX_IMAGE_PIXELS": "0"},
        {"AMA_VISUAL_ANALYSIS_MAX_OUTPUT_TOKENS": "5001"},
    ],
)
def test_settings_reject_invalid_visual_analysis_configuration(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="VISUAL_ANALYSIS|OPENAI_API_KEY"):
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


@pytest.mark.parametrize(
    "environment",
    [
        {"AMA_RETRIEVAL_CANDIDATE_LIMIT": "0"},
        {"AMA_RETRIEVAL_RRF_K": "0"},
        {"AMA_RETRIEVAL_SEMANTIC_WEIGHT": "-1"},
        {"AMA_RETRIEVAL_TEXT_WEIGHT": "many"},
        {"AMA_RETRIEVAL_TEXT_WEIGHT": "nan"},
        {
            "AMA_RETRIEVAL_SEMANTIC_WEIGHT": "0",
            "AMA_RETRIEVAL_TEXT_WEIGHT": "0",
        },
    ],
)
def test_settings_reject_invalid_retrieval_configuration(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="RETRIEVAL|retrieval"):
        Settings.from_environment(environment)
