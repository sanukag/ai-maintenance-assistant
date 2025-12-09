"""Deterministic test doubles shared across integration tests."""

from collections.abc import Sequence

from maintenance_assistant.answering import GeneratedAnswer, GroundingSource
from maintenance_assistant.embeddings import EmbeddingBatch


class KeywordEmbeddingProvider:
    """Map maintenance keywords to small deterministic vectors."""

    model = "test-embedding"
    dimensions = 3

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls.append(tuple(texts))
        vectors = tuple(self._vector(text) for text in texts)
        return EmbeddingBatch(
            model=self.model,
            dimensions=self.dimensions,
            vectors=vectors,
            input_tokens=sum(len(text.split()) for text in texts),
        )

    @staticmethod
    def _vector(text: str) -> tuple[float, float, float]:
        lowered = text.lower()
        pump = 1.0 if "pump" in lowered else 0.0
        valve = 1.0 if "valve" in lowered else 0.0
        motor = 1.0 if "motor" in lowered else 0.0
        if pump == valve == motor == 0.0:
            return (0.1, 0.1, 0.1)
        return (pump, valve, motor)


class FixedAnswerProvider:
    """Return one configurable grounded-answer payload for integration tests."""

    model = "test-answer"

    def __init__(
        self,
        *,
        answerable: bool = True,
        answer: str = "Isolate the pump before maintenance [S1].",
        citation_ids: tuple[str, ...] = ("S1",),
    ) -> None:
        self.answerable = answerable
        self.answer_text = answer
        self.citation_ids = citation_ids
        self.calls: list[tuple[str, tuple[GroundingSource, ...]]] = []

    def generate(
        self,
        question: str,
        sources: Sequence[GroundingSource],
    ) -> GeneratedAnswer:
        prepared_sources = tuple(sources)
        self.calls.append((question, prepared_sources))
        return GeneratedAnswer(
            answerable=self.answerable,
            answer=self.answer_text,
            citation_ids=self.citation_ids,
            model=self.model,
            input_tokens=24,
            output_tokens=8,
        )
