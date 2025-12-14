"""Persistent, auditable state for guided maintenance diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
import sqlite3
from typing import Any
from uuid import uuid4

from maintenance_assistant.ingestion import (
    DocumentMetadata,
    IngestionError,
    IngestionErrorCode,
    LocalDocumentStore,
)


class DiagnosticStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class DiagnosticSafetyStatus(StrEnum):
    UNKNOWN = "unknown"
    NON_INTRUSIVE_ONLY = "non_intrusive_only"
    CONFIRMED_SAFE = "confirmed_safe"
    STOP = "stop"


class DiagnosticRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class DiagnosticAction(StrEnum):
    ASK_QUESTION = "ask_question"
    REQUEST_OBSERVATION = "request_observation"
    REQUEST_MEASUREMENT = "request_measurement"
    SUGGEST_CHECK = "suggest_check"
    ANSWER_QUESTION = "answer_question"
    REPORT_DIAGNOSIS = "report_diagnosis"
    ESCALATE = "escalate"
    MARK_RESOLVED = "mark_resolved"


class DiagnosticLikelihood(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class DiagnosticMeasurement:
    name: str
    value: str
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticHypothesis:
    title: str
    likelihood: DiagnosticLikelihood
    rationale: str
    supporting_source_ids: tuple[str, ...] = ()
    contrary_observations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticState:
    symptoms: tuple[str, ...] = ()
    observations: tuple[str, ...] = ()
    measurements: tuple[DiagnosticMeasurement, ...] = ()
    completed_checks: tuple[str, ...] = ()
    hypotheses: tuple[DiagnosticHypothesis, ...] = ()
    summary: str = ""


@dataclass(frozen=True, slots=True)
class DiagnosticTurn:
    id: str
    session_id: str
    sequence: int
    role: DiagnosticRole
    content: str
    action: DiagnosticAction | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DiagnosticSession:
    id: str
    title: str
    status: DiagnosticStatus
    safety_status: DiagnosticSafetyStatus
    document_id: str | None
    metadata: DocumentMetadata
    state: DiagnosticState
    created_at: datetime
    updated_at: datetime
    turn_count: int


@dataclass(frozen=True, slots=True)
class DiagnosticSessionDetail:
    session: DiagnosticSession
    turns: tuple[DiagnosticTurn, ...]


class DiagnosticStore:
    """Store diagnostic state and an immutable turn ledger in SQLite."""

    def __init__(self, document_store: LocalDocumentStore) -> None:
        self.document_store = document_store

    def create_session(
        self,
        initial_message: str,
        *,
        document_id: str | None = None,
        metadata: DocumentMetadata = DocumentMetadata(),
        safety_status: DiagnosticSafetyStatus = DiagnosticSafetyStatus.UNKNOWN,
    ) -> DiagnosticSessionDetail:
        message = _normalise_text(initial_message, "Initial diagnostic message", 2_000)
        self.document_store.initialise()
        if document_id is not None and self.document_store.get_document(document_id) is None:
            raise KeyError(document_id)
        session_id = str(uuid4())
        turn_id = str(uuid4())
        now = datetime.now(UTC)
        title = " ".join(message.split())[:80]
        state = DiagnosticState(symptoms=(message,), summary=message)
        try:
            with self.document_store._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO diagnostic_sessions (
                           id, title, status, safety_status, document_id,
                           metadata_json, state_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        title,
                        DiagnosticStatus.ACTIVE.value,
                        safety_status.value,
                        document_id,
                        _metadata_json(metadata),
                        _state_json(state),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """INSERT INTO diagnostic_turns (
                           id, session_id, sequence, role, content, action,
                           payload_json, created_at
                       ) VALUES (?, ?, 0, ?, ?, NULL, '{}', ?)""",
                    (turn_id, session_id, DiagnosticRole.USER.value, message, now.isoformat()),
                )
        except sqlite3.Error as error:
            raise _storage_error("The diagnostic session could not be created", error)
        detail = self.get_session(session_id)
        if detail is None:  # pragma: no cover - defensive postcondition
            raise _storage_error("The diagnostic session could not be read")
        return detail

    def append_exchange(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str,
        action: DiagnosticAction,
        state: DiagnosticState,
        payload: dict[str, Any],
        status: DiagnosticStatus = DiagnosticStatus.ACTIVE,
        safety_status: DiagnosticSafetyStatus | None = None,
    ) -> DiagnosticSessionDetail:
        user_content = _normalise_text(user_message, "Diagnostic message", 2_000)
        assistant_content = _normalise_text(
            assistant_message, "Diagnostic response", 8_000
        )
        now = datetime.now(UTC)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            state_json = _state_json(state)
        except (TypeError, ValueError) as error:
            raise ValueError("Diagnostic state must be JSON serialisable") from error
        try:
            with self.document_store._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT safety_status FROM diagnostic_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(session_id)
                next_sequence = connection.execute(
                    """SELECT COALESCE(MAX(sequence), -1) + 1
                       FROM diagnostic_turns WHERE session_id = ?""",
                    (session_id,),
                ).fetchone()[0]
                connection.executemany(
                    """INSERT INTO diagnostic_turns (
                           id, session_id, sequence, role, content, action,
                           payload_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        (
                            str(uuid4()), session_id, next_sequence,
                            DiagnosticRole.USER.value, user_content, None, "{}",
                            now.isoformat(),
                        ),
                        (
                            str(uuid4()), session_id, next_sequence + 1,
                            DiagnosticRole.ASSISTANT.value, assistant_content,
                            action.value, payload_json, now.isoformat(),
                        ),
                    ),
                )
                connection.execute(
                    """UPDATE diagnostic_sessions
                       SET status = ?, safety_status = ?, state_json = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        status.value,
                        (safety_status or DiagnosticSafetyStatus(row["safety_status"])).value,
                        state_json,
                        now.isoformat(),
                        session_id,
                    ),
                )
        except KeyError:
            raise
        except sqlite3.Error as error:
            raise _storage_error("The diagnostic exchange could not be saved", error)
        detail = self.get_session(session_id)
        if detail is None:  # pragma: no cover - defensive postcondition
            raise _storage_error("The diagnostic session could not be read")
        return detail

    def get_session(self, session_id: str) -> DiagnosticSessionDetail | None:
        self.document_store.initialise()
        try:
            with self.document_store._connection() as connection:
                row = connection.execute(
                    """SELECT diagnostic_sessions.*, COUNT(diagnostic_turns.id) AS turn_count
                       FROM diagnostic_sessions
                       LEFT JOIN diagnostic_turns
                         ON diagnostic_turns.session_id = diagnostic_sessions.id
                       WHERE diagnostic_sessions.id = ?
                       GROUP BY diagnostic_sessions.id""",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return None
                turn_rows = connection.execute(
                    """SELECT * FROM diagnostic_turns
                       WHERE session_id = ? ORDER BY sequence""",
                    (session_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise _storage_error("The diagnostic session could not be read", error)
        return DiagnosticSessionDetail(
            session=_session_from_row(row),
            turns=tuple(_turn_from_row(item) for item in turn_rows),
        )

    def list_sessions(self, *, limit: int = 50, offset: int = 0) -> tuple[DiagnosticSession, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must not be negative")
        self.document_store.initialise()
        try:
            with self.document_store._connection() as connection:
                rows = connection.execute(
                    """SELECT diagnostic_sessions.*, COUNT(diagnostic_turns.id) AS turn_count
                       FROM diagnostic_sessions
                       LEFT JOIN diagnostic_turns
                         ON diagnostic_turns.session_id = diagnostic_sessions.id
                       GROUP BY diagnostic_sessions.id
                       ORDER BY diagnostic_sessions.updated_at DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error as error:
            raise _storage_error("Diagnostic sessions could not be listed", error)
        return tuple(_session_from_row(row) for row in rows)

    def delete_session(self, session_id: str) -> bool:
        self.document_store.initialise()
        try:
            with self.document_store._connection() as connection:
                deleted = connection.execute(
                    "DELETE FROM diagnostic_sessions WHERE id = ?", (session_id,)
                ).rowcount
        except sqlite3.Error as error:
            raise _storage_error("The diagnostic session could not be deleted", error)
        return deleted == 1


def _normalise_text(value: str, label: str, maximum: int) -> str:
    normalised = value.strip()
    if not normalised:
        raise ValueError(f"{label} must contain text")
    if len(normalised) > maximum:
        raise ValueError(f"{label} must not exceed {maximum} characters")
    return normalised


def _metadata_json(metadata: DocumentMetadata) -> str:
    return json.dumps(
        {
            "brand": list(metadata.brand),
            "machine": list(metadata.machine),
            "site": list(metadata.site),
            "document_type": list(metadata.document_type),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _state_json(state: DiagnosticState) -> str:
    return json.dumps(
        {
            "symptoms": list(state.symptoms),
            "observations": list(state.observations),
            "measurements": [
                {"name": item.name, "value": item.value, "unit": item.unit}
                for item in state.measurements
            ],
            "completed_checks": list(state.completed_checks),
            "hypotheses": [
                {
                    "title": item.title,
                    "likelihood": item.likelihood.value,
                    "rationale": item.rationale,
                    "supporting_source_ids": list(item.supporting_source_ids),
                    "contrary_observations": list(item.contrary_observations),
                }
                for item in state.hypotheses
            ],
            "summary": state.summary,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _state_from_json(value: str) -> DiagnosticState:
    try:
        payload = json.loads(value)
        return DiagnosticState(
            symptoms=tuple(payload.get("symptoms", ())),
            observations=tuple(payload.get("observations", ())),
            measurements=tuple(
                DiagnosticMeasurement(
                    name=item["name"], value=item["value"], unit=item.get("unit")
                )
                for item in payload.get("measurements", ())
            ),
            completed_checks=tuple(payload.get("completed_checks", ())),
            hypotheses=tuple(
                DiagnosticHypothesis(
                    title=item["title"],
                    likelihood=DiagnosticLikelihood(item["likelihood"]),
                    rationale=item["rationale"],
                    supporting_source_ids=tuple(item.get("supporting_source_ids", ())),
                    contrary_observations=tuple(item.get("contrary_observations", ())),
                )
                for item in payload.get("hypotheses", ())
            ),
            summary=payload.get("summary", ""),
        )
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise _storage_error("Stored diagnostic state is invalid", error)


def _session_from_row(row: sqlite3.Row) -> DiagnosticSession:
    try:
        metadata_payload = json.loads(row["metadata_json"])
        metadata = DocumentMetadata(
            brand=metadata_payload.get("brand", ()),
            machine=metadata_payload.get("machine", ()),
            site=metadata_payload.get("site", ()),
            document_type=metadata_payload.get("document_type", ()),
        )
        return DiagnosticSession(
            id=row["id"],
            title=row["title"],
            status=DiagnosticStatus(row["status"]),
            safety_status=DiagnosticSafetyStatus(row["safety_status"]),
            document_id=row["document_id"],
            metadata=metadata,
            state=_state_from_json(row["state_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            turn_count=row["turn_count"],
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise _storage_error("Stored diagnostic session is invalid", error)


def _turn_from_row(row: sqlite3.Row) -> DiagnosticTurn:
    try:
        return DiagnosticTurn(
            id=row["id"],
            session_id=row["session_id"],
            sequence=row["sequence"],
            role=DiagnosticRole(row["role"]),
            content=row["content"],
            action=DiagnosticAction(row["action"]) if row["action"] else None,
            payload=json.loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise _storage_error("Stored diagnostic turn is invalid", error)


def _storage_error(message: str, cause: BaseException | None = None) -> IngestionError:
    error = IngestionError(IngestionErrorCode.STORAGE_FAILED, message)
    if cause is not None:
        error.__cause__ = cause
    return error
