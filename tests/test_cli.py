from pathlib import Path

from maintenance_assistant.cli import main


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


def test_cli_reports_ingestion_error(tmp_path: Path, capsys) -> None:
    exit_code = main([str(tmp_path / "missing.pdf")])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "[file_not_found]" in output.err
