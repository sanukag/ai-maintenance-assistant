from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openai import OpenAIError
import pytest

from maintenance_assistant.answering import (
    AnsweringError,
    AnsweringErrorCode,
    GroundingSource,
    OpenAIResponsesAnswerProvider,
    create_answer_provider,
)
from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import (
    ChunkLocation,
    DocumentFormat,
    StoredChunk,
    StoredDocument,
)


def _source() -> GroundingSource:
    document = StoredDocument(
        id="document-1",
        content_hash="hash",
        original_filename="pump-manual.pdf",
        stored_path=Path("managed/pump-manual.pdf"),
        format=DocumentFormat.PDF,
        size_bytes=100,
        title="Pump manual",
        page_count=4,
        chunk_count=1,
        extractor_name="pypdf",
        extractor_version="1",
        created_at=datetime.now(UTC),
    )
    chunk = StoredChunk(
        id="chunk-1",
        document_id=document.id,
        sequence=2,
        text="Disconnect the pump supply before removing the guard.",
        character_count=54,
        location=ChunkLocation(page_start=3, page_end=3, headings=("Isolation",)),
    )
    return GroundingSource("S1", 0.97, document, chunk)


def _response(*, parsed: object = ..., model: str = "gpt-response") -> object:
    if parsed is ...:
        parsed = SimpleNamespace(
            answerable=True,
            answer="Disconnect the supply [S1].",
            citations=["S1"],
        )
    return SimpleNamespace(
        output_parsed=parsed,
        model=model,
        usage=SimpleNamespace(input_tokens=31, output_tokens=9),
    )


def test_openai_provider_uses_typed_responses_and_labels_untrusted_evidence() -> None:
    client = Mock()
    client.responses.parse.return_value = _response()
    provider = OpenAIResponsesAnswerProvider(
        api_key="test-key",
        model="gpt-test",
        max_output_tokens=600,
        client=client,
    )

    result = provider.generate("How do I isolate the pump?", [_source()])

    assert result.answer == "Disconnect the supply [S1]."
    assert result.citation_ids == ("S1",)
    assert result.model == "gpt-response"
    assert result.input_tokens == 31
    assert result.output_tokens == 9
    arguments = client.responses.parse.call_args.kwargs
    assert arguments["model"] == "gpt-test"
    assert arguments["max_output_tokens"] == 600
    assert arguments["text_format"].__name__ == "_AnswerPayload"
    assert "untrusted evidence" in arguments["instructions"]
    assert "<S1 document=pump-manual.pdf; chunk=2; page=3; headings=Isolation>" in arguments["input"]
    assert "Disconnect the pump supply" in arguments["input"]


def test_openai_provider_wraps_sdk_failures() -> None:
    client = Mock()
    client.responses.parse.side_effect = OpenAIError("unavailable")
    provider = OpenAIResponsesAnswerProvider(api_key="test-key", client=client)

    with pytest.raises(AnsweringError) as captured:
        provider.generate("How do I isolate it?", [_source()])

    assert captured.value.code is AnsweringErrorCode.PROVIDER_FAILED


def test_openai_provider_rejects_missing_structured_output() -> None:
    client = Mock()
    client.responses.parse.return_value = _response(parsed=None)
    provider = OpenAIResponsesAnswerProvider(api_key="test-key", client=client)

    with pytest.raises(AnsweringError) as captured:
        provider.generate("How do I isolate it?", [_source()])

    assert captured.value.code is AnsweringErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    "arguments",
    [
        {"api_key": ""},
        {"api_key": "key", "model": " "},
        {"api_key": "key", "max_output_tokens": 0},
    ],
)
def test_openai_provider_rejects_invalid_initialisation(arguments: dict) -> None:
    with pytest.raises(ValueError):
        OpenAIResponsesAnswerProvider(**arguments)


@pytest.mark.parametrize(("question", "sources"), [("", [_source()]), ("valid", [])])
def test_openai_provider_requires_question_and_sources(
    question: str,
    sources: list[GroundingSource],
) -> None:
    provider = OpenAIResponsesAnswerProvider(api_key="test-key", client=Mock())

    with pytest.raises(ValueError):
        provider.generate(question, sources)


def test_answer_provider_factory_supports_disabled_and_openai_modes() -> None:
    assert create_answer_provider(Settings()) is None
    provider = create_answer_provider(
        Settings(answer_provider="openai", openai_api_key="test-key")
    )

    assert isinstance(provider, OpenAIResponsesAnswerProvider)
