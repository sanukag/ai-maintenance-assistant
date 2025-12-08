from pathlib import Path

from maintenance_assistant.cli import main
from maintenance_assistant.cli import search_main
from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import IngestionService
from tests.fakes import KeywordEmbeddingProvider


def test_cli_ingests_document(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Check the pump seal.", encoding="utf-8")
    monkeypatch.setenv("AMA_DATA_DIRECTORY", str(tmp_path / "data"))

    exit_code = main([str(path)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert "Ingested pump.txt" in output.out
    assert "(1 chunk)" in output.out
    assert "Embeddings are disabled" in output.out


def test_cli_reports_ingestion_error(tmp_path: Path, capsys) -> None:
    exit_code = main([str(tmp_path / "missing.pdf")])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "[file_not_found]" in output.err


def test_cli_reports_stored_vectors(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    path = tmp_path / "pump.txt"
    path.write_text("Check the pump seal.", encoding="utf-8")
    monkeypatch.setenv("AMA_DATA_DIRECTORY", str(tmp_path / "data"))
    monkeypatch.setenv("AMA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "maintenance_assistant.cli.create_embedding_provider",
        lambda settings: KeywordEmbeddingProvider(),
    )

    exit_code = main([str(path)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "Stored 1 vector using test-embedding" in output.out


def test_search_cli_returns_ranked_chunk(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    path = tmp_path / "procedures.txt"
    path.write_text("Pump seal replacement.", encoding="utf-8")
    settings = Settings(data_directory=tmp_path / "data")
    IngestionService(
        settings,
        embedding_provider=KeywordEmbeddingProvider(),
    ).ingest(path)
    monkeypatch.setenv("AMA_DATA_DIRECTORY", str(settings.data_directory))
    monkeypatch.setenv("AMA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "maintenance_assistant.cli.create_embedding_provider",
        lambda configured: KeywordEmbeddingProvider(),
    )

    exit_code = search_main(["pump repair", "--limit", "1"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "procedures.txt" in output.out
    assert "Pump seal replacement." in output.out
    assert "score 1.000" in output.out


def test_search_cli_requires_embedding_provider(capsys) -> None:
    exit_code = search_main(["pump"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "AMA_EMBEDDING_PROVIDER=openai" in output.err
