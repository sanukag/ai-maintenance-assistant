from pathlib import Path
import sqlite3

import pytest

from maintenance_assistant.diagnostics import (
    DiagnosticAction,
    DiagnosticHypothesis,
    DiagnosticLikelihood,
    DiagnosticMeasurement,
    DiagnosticSafetyStatus,
    DiagnosticState,
    DiagnosticStore,
)
from maintenance_assistant.ingestion import DocumentMetadata, IngestionError, LocalDocumentStore


def _store(tmp_path: Path) -> DiagnosticStore:
    return DiagnosticStore(LocalDocumentStore(tmp_path / "data"))


def test_diagnostic_session_persists_scope_and_initial_symptom(tmp_path: Path) -> None:
    diagnostics = _store(tmp_path)
    metadata = DocumentMetadata(brand=("Acme",), machine=("P-100",))
    created = diagnostics.create_session(
        "  Pump trips after five minutes.  ",
        metadata=metadata,
        safety_status=DiagnosticSafetyStatus.NON_INTRUSIVE_ONLY,
    )

    restarted = _store(tmp_path).get_session(created.session.id)

    assert restarted is not None
    assert restarted.session.title == "Pump trips after five minutes."
    assert restarted.session.metadata == metadata
    assert restarted.session.state.symptoms == ("Pump trips after five minutes.",)
    assert restarted.session.safety_status == DiagnosticSafetyStatus.NON_INTRUSIVE_ONLY
    assert restarted.turns[0].content == "Pump trips after five minutes."


def test_exchange_atomically_updates_state_and_turn_ledger(tmp_path: Path) -> None:
    diagnostics = _store(tmp_path)
    created = diagnostics.create_session("Motor will not start")
    state = DiagnosticState(
        symptoms=("Motor will not start",),
        observations=("Contactor closes",),
        measurements=(DiagnosticMeasurement("Supply voltage", "398", "V"),),
        completed_checks=("Inspected the emergency stop",),
        hypotheses=(
            DiagnosticHypothesis(
                "Overload relay open",
                DiagnosticLikelihood.MEDIUM,
                "The contactor closes but the motor remains stopped.",
                ("S1",),
            ),
        ),
        summary="The contactor closes but the motor does not turn.",
    )

    updated = diagnostics.append_exchange(
        created.session.id,
        user_message="The contactor closes and voltage is 398 V",
        assistant_message="Is the overload relay showing a trip indication? [S1]",
        action=DiagnosticAction.ASK_QUESTION,
        state=state,
        payload={"citations": [{"source_id": "S1"}]},
        safety_status=DiagnosticSafetyStatus.CONFIRMED_SAFE,
    )

    assert [turn.sequence for turn in updated.turns] == [0, 1, 2]
    assert updated.turns[-1].action == DiagnosticAction.ASK_QUESTION
    assert updated.turns[-1].payload["citations"][0]["source_id"] == "S1"
    assert updated.session.state == state
    assert updated.session.safety_status == DiagnosticSafetyStatus.CONFIRMED_SAFE


def test_sessions_list_newest_first_and_delete_cascades(tmp_path: Path) -> None:
    diagnostics = _store(tmp_path)
    first = diagnostics.create_session("First fault")
    second = diagnostics.create_session("Second fault")
    diagnostics.append_exchange(
        first.session.id,
        user_message="More detail",
        assistant_message="Please inspect the visible status indicator.",
        action=DiagnosticAction.REQUEST_OBSERVATION,
        state=first.session.state,
        payload={},
    )

    listed = diagnostics.list_sessions()

    assert [item.id for item in listed] == [first.session.id, second.session.id]
    assert diagnostics.delete_session(first.session.id) is True
    assert diagnostics.delete_session(first.session.id) is False
    assert diagnostics.get_session(first.session.id) is None
    with diagnostics.document_store._connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM diagnostic_turns WHERE session_id = ?",
            (first.session.id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("message", ["", "   ", "x" * 2_001])
def test_session_rejects_invalid_initial_message(tmp_path: Path, message: str) -> None:
    with pytest.raises(ValueError):
        _store(tmp_path).create_session(message)


def test_missing_document_scope_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        _store(tmp_path).create_session("Pump is noisy", document_id="missing")


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    diagnostics = _store(tmp_path)
    created = diagnostics.create_session("Pump is noisy")
    with diagnostics.document_store._connection() as connection:
        connection.execute(
            "UPDATE diagnostic_sessions SET state_json = ? WHERE id = ?",
            ("not-json", created.session.id),
        )

    with pytest.raises(IngestionError, match="Stored diagnostic state is invalid"):
        diagnostics.get_session(created.session.id)


def test_schema_migration_creates_diagnostic_tables(tmp_path: Path) -> None:
    document_store = LocalDocumentStore(tmp_path / "data")
    document_store.initialise()

    with sqlite3.connect(document_store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"diagnostic_sessions", "diagnostic_turns"} <= tables
