from pathlib import Path

import pytest

from maintenance_assistant.answering import (
    AnsweringError,
    AnsweringErrorCode,
    GroundedAnswerService,
)
from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import IngestionService, LocalDocumentStore
from maintenance_assistant.retrieval import VectorSearchService
from tests.fakes import FixedAnswerProvider, KeywordEmbeddingProvider


def _service(tmp_path: Path, provider: FixedAnswerProvider) -> GroundedAnswerService:
    document = tmp_path / "pump-manual.txt"
    document.write_text(
        "Pump isolation procedure.\n\nValve inspection procedure.",
        encoding="utf-8",
    )
    settings = Settings(
        data_directory=tmp_path / "data",
        chunk_size_characters=30,
        chunk_overlap_characters=0,
    )
    embeddings = KeywordEmbeddingProvider()
    IngestionService(settings, embedding_provider=embeddings).ingest(document)
    return GroundedAnswerService(
        VectorSearchService(LocalDocumentStore(settings.data_directory), embeddings),
        provider,
    )


def test_grounded_answer_maps_verified_markers_to_local_chunks(tmp_path: Path) -> None:
    provider = FixedAnswerProvider()
    service = _service(tmp_path, provider)

    result = service.answer("How do I maintain the pump?", max_sources=1)

    assert result.answerable is True
    assert result.answer == "Isolate the pump before maintenance [S1]."
    assert result.model == "test-answer"
    assert result.input_tokens == 24
    assert result.output_tokens == 8
    assert len(result.citations) == 1
    assert result.citations[0].source_id == "S1"
    assert result.citations[0].document.original_filename == "pump-manual.txt"
    assert result.citations[0].chunk.text == "Pump isolation procedure."
    assert provider.calls[0][0] == "How do I maintain the pump?"
    assert provider.calls[0][1][0].source_id == "S1"


def test_no_retrieved_evidence_returns_local_refusal_without_provider_call(
    tmp_path: Path,
) -> None:
    provider = FixedAnswerProvider()
    service = _service(tmp_path, provider)

    result = service.answer("How do I maintain it?", document_id="missing")

    assert result.answerable is False
    assert result.citations == ()
    assert "do not contain enough evidence" in result.answer
    assert result.input_tokens == 0
    assert provider.calls == []


def test_provider_can_report_insufficient_evidence(tmp_path: Path) -> None:
    provider = FixedAnswerProvider(
        answerable=False,
        answer="Unsupported provider wording",
        citation_ids=(),
    )
    service = _service(tmp_path, provider)

    result = service.answer("What is the motor torque?")

    assert result.answerable is False
    assert result.answer != "Unsupported provider wording"
    assert result.citations == ()
    assert result.input_tokens == 24


@pytest.mark.parametrize(
    ("answerable", "answer", "citations"),
    [
        (True, "", ("S1",)),
        (True, "No marker present", ("S1",)),
        (True, "Claim [S1].", ()),
        (True, "Claim [S1].", ("S1", "S1")),
        (True, "Claim [S1].", ("S2",)),
        (True, "Claim [S2].", ("S2",)),
        (False, "No answer", ("S1",)),
    ],
)
def test_service_rejects_unverifiable_provider_responses(
    tmp_path: Path,
    answerable: bool,
    answer: str,
    citations: tuple[str, ...],
) -> None:
    service = _service(
        tmp_path,
        FixedAnswerProvider(
            answerable=answerable,
            answer=answer,
            citation_ids=citations,
        ),
    )

    with pytest.raises(AnsweringError) as captured:
        service.answer("How do I maintain the pump?", max_sources=1)

    assert captured.value.code is AnsweringErrorCode.INVALID_RESPONSE
