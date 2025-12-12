"""Durable local storage for worker and assistant conversation history."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
import sqlite3
from typing import Any
from uuid import uuid4

from maintenance_assistant.answering import GroundedAnswer
from maintenance_assistant.ingestion import (
    IngestionError,
    IngestionErrorCode,
    LocalDocumentStore,
    DocumentMetadata,
)


class ConversationRole(StrEnum):
    """Roles retained in the ordered local message ledger."""

    USER = "user"
    ASSISTANT = "assistant"


class ResponseFeedback(StrEnum):
    """A worker's current rating for one assistant response."""

    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class ConversationCitation:
    """A durable citation snapshot that survives manual lifecycle changes."""

    source_id: str
    score: float
    document_id: str
    document_title: str
    original_filename: str
    chunk_id: str
    chunk_sequence: int
    parent_context_id: str | None
    excerpt: str
    page_start: int | None
    page_end: int | None
    headings: tuple[str, ...]
    line_start: int | None
    line_end: int | None


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One immutable user or assistant message in a conversation."""

    id: str
    conversation_id: str
    sequence: int
    role: ConversationRole
    content: str
    created_at: datetime
    scope_document_id: str | None = None
    answerable: bool | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    citations: tuple[ConversationCitation, ...] = ()
    feedback: ResponseFeedback | None = None
    scope_metadata: DocumentMetadata = DocumentMetadata()


@dataclass(frozen=True, slots=True)
class Conversation:
    """Conversation metadata used by history lists and detail views."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


@dataclass(frozen=True, slots=True)
class ConversationDetail:
    """Conversation metadata and every ordered stored message."""

    conversation: Conversation
    messages: tuple[ConversationMessage, ...]


class ConversationStore:
    """Persist complete successful answer exchanges in the application SQLite DB."""

    def __init__(self, document_store: LocalDocumentStore) -> None:
        self.document_store = document_store

    def record_exchange(
        self,
        answer: GroundedAnswer,
        *,
        conversation_id: str | None = None,
        scope_document_id: str | None = None,
        scope_metadata: DocumentMetadata = DocumentMetadata(),
    ) -> ConversationDetail:
        """Atomically append one user question and its assistant response."""

        question = answer.question.strip()
        response = answer.answer.strip()
        if not question or not response:
            raise ValueError("Conversation messages must not be empty")
        self.document_store.initialise()
        now = datetime.now(UTC)
        selected_id = conversation_id or str(uuid4())
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT id FROM conversations WHERE id = ?",
                    (selected_id,),
                ).fetchone()
                if conversation_id is not None and existing is None:
                    raise KeyError(conversation_id)
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO conversations (id, title, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            selected_id,
                            _conversation_title(question),
                            now.isoformat(),
                            now.isoformat(),
                        ),
                    )
                    next_sequence = 0
                else:
                    row = connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), -1) + 1 AS next_sequence
                        FROM conversation_messages WHERE conversation_id = ?
                        """,
                        (selected_id,),
                    ).fetchone()
                    next_sequence = int(row["next_sequence"])
                    connection.execute(
                        "UPDATE conversations SET updated_at = ? WHERE id = ?",
                        (now.isoformat(), selected_id),
                    )
                connection.executemany(
                    """
                    INSERT INTO conversation_messages (
                        id, conversation_id, sequence, role, content, created_at,
                        scope_document_id, answerable, model, input_tokens,
                        output_tokens, citations_json, scope_metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            str(uuid4()),
                            selected_id,
                            next_sequence,
                            ConversationRole.USER.value,
                            question,
                            now.isoformat(),
                            scope_document_id,
                            None,
                            None,
                            None,
                            None,
                            "[]",
                            _metadata_json(scope_metadata),
                        ),
                        (
                            str(uuid4()),
                            selected_id,
                            next_sequence + 1,
                            ConversationRole.ASSISTANT.value,
                            response,
                            now.isoformat(),
                            scope_document_id,
                            int(answer.answerable),
                            answer.model,
                            answer.input_tokens,
                            answer.output_tokens,
                            json.dumps(_citation_payloads(answer)),
                            _metadata_json(scope_metadata),
                        ),
                    ),
                )
        except KeyError:
            raise
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Conversation history could not be saved locally",
            ) from error
        detail = self.get_conversation(selected_id)
        if detail is None:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Saved conversation history could not be read",
            )
        return detail

    def list_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        """Return conversations from most recently updated to oldest."""

        if limit < 1:
            raise ValueError("limit must be greater than zero")
        if offset < 0:
            raise ValueError("offset must be zero or greater")
        self.document_store.initialise()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT conversations.*, COUNT(conversation_messages.id) AS message_count
                    FROM conversations
                    LEFT JOIN conversation_messages
                        ON conversation_messages.conversation_id = conversations.id
                    GROUP BY conversations.id
                    ORDER BY conversations.updated_at DESC, conversations.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
                conversations = tuple(_conversation_from_row(row) for row in rows)
        except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Conversation history could not be queried",
            ) from error
        return conversations

    def get_conversation(self, conversation_id: str) -> ConversationDetail | None:
        """Return one conversation and all of its messages in order."""

        self.document_store.initialise()
        try:
            with self._connection() as connection:
                conversation_row = connection.execute(
                    """
                    SELECT conversations.*, COUNT(conversation_messages.id) AS message_count
                    FROM conversations
                    LEFT JOIN conversation_messages
                        ON conversation_messages.conversation_id = conversations.id
                    WHERE conversations.id = ?
                    GROUP BY conversations.id
                    """,
                    (conversation_id,),
                ).fetchone()
                if conversation_row is None:
                    return None
                message_rows = connection.execute(
                    """
                    SELECT conversation_messages.*,
                           conversation_message_feedback.rating AS feedback
                    FROM conversation_messages
                    LEFT JOIN conversation_message_feedback
                      ON conversation_message_feedback.message_id = conversation_messages.id
                    WHERE conversation_messages.conversation_id = ?
                    ORDER BY conversation_messages.sequence
                    """,
                    (conversation_id,),
                ).fetchall()
                detail = ConversationDetail(
                    conversation=_conversation_from_row(conversation_row),
                    messages=tuple(_message_from_row(row) for row in message_rows),
                )
        except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Conversation history could not be queried",
            ) from error
        return detail

    def set_response_feedback(
        self,
        conversation_id: str,
        message_id: str,
        rating: ResponseFeedback,
    ) -> ResponseFeedback:
        """Create or replace the rating for one assistant response."""

        self.document_store.initialise()
        now = datetime.now(UTC).isoformat()
        try:
            with self._connection() as connection:
                message = connection.execute(
                    """
                    SELECT role FROM conversation_messages
                    WHERE id = ? AND conversation_id = ?
                    """,
                    (message_id, conversation_id),
                ).fetchone()
                if message is None:
                    raise KeyError(message_id)
                if message["role"] != ConversationRole.ASSISTANT.value:
                    raise ValueError("Feedback can only be recorded for assistant responses")
                connection.execute(
                    """
                    INSERT INTO conversation_message_feedback (
                        id, conversation_id, message_id, rating, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        rating = excluded.rating,
                        updated_at = excluded.updated_at
                    """,
                    (str(uuid4()), conversation_id, message_id, rating.value, now, now),
                )
        except (KeyError, ValueError):
            raise
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Response feedback could not be saved locally",
            ) from error
        return rating

    def clear_response_feedback(self, conversation_id: str, message_id: str) -> None:
        """Clear a response rating while retaining the conversation message."""

        self.document_store.initialise()
        try:
            with self._connection() as connection:
                message = connection.execute(
                    """
                    SELECT role FROM conversation_messages
                    WHERE id = ? AND conversation_id = ?
                    """,
                    (message_id, conversation_id),
                ).fetchone()
                if message is None:
                    raise KeyError(message_id)
                if message["role"] != ConversationRole.ASSISTANT.value:
                    raise ValueError("Feedback can only be cleared for assistant responses")
                connection.execute(
                    "DELETE FROM conversation_message_feedback WHERE message_id = ?",
                    (message_id,),
                )
        except (KeyError, ValueError):
            raise
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Response feedback could not be cleared locally",
            ) from error

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and its cascading message history."""

        self.document_store.initialise()
        try:
            with self._connection() as connection:
                deleted = connection.execute(
                    "DELETE FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).rowcount
        except sqlite3.Error as error:
            raise IngestionError(
                IngestionErrorCode.STORAGE_FAILED,
                "Conversation history could not be deleted",
            ) from error
        return deleted == 1

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.document_store.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _conversation_title(question: str, maximum_length: int = 80) -> str:
    title = " ".join(question.split())
    if len(title) <= maximum_length:
        return title
    return f"{title[: maximum_length - 1].rstrip()}…"


def _citation_payloads(answer: GroundedAnswer) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for citation in answer.citations:
        evidence = citation.parent or citation.chunk
        location = evidence.location
        payloads.append(
            {
                "source_id": citation.source_id,
                "score": citation.score,
                "document_id": citation.document.id,
                "document_title": citation.document.title,
                "original_filename": citation.document.original_filename,
                "chunk_id": citation.chunk.id,
                "chunk_sequence": citation.chunk.sequence,
                "parent_context_id": (
                    citation.parent.id if citation.parent is not None else None
                ),
                "excerpt": evidence.text,
                "page_start": location.page_start,
                "page_end": location.page_end,
                "headings": list(location.headings),
                "line_start": location.line_start,
                "line_end": location.line_end,
            }
        )
    return payloads


def _metadata_json(metadata: DocumentMetadata) -> str:
    return json.dumps(
        {
            "brand": metadata.brand,
            "machine": metadata.machine,
            "site": metadata.site,
            "document_type": metadata.document_type,
        }
    )


def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        message_count=row["message_count"],
    )


def _message_from_row(row: sqlite3.Row) -> ConversationMessage:
    citations = json.loads(row["citations_json"])
    scope_metadata = json.loads(row["scope_metadata_json"])
    return ConversationMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sequence=row["sequence"],
        role=ConversationRole(row["role"]),
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
        scope_document_id=row["scope_document_id"],
        answerable=(bool(row["answerable"]) if row["answerable"] is not None else None),
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        citations=tuple(
            ConversationCitation(
                source_id=item["source_id"],
                score=float(item["score"]),
                document_id=item["document_id"],
                document_title=item["document_title"],
                original_filename=item["original_filename"],
                chunk_id=item["chunk_id"],
                chunk_sequence=item["chunk_sequence"],
                parent_context_id=item["parent_context_id"],
                excerpt=item["excerpt"],
                page_start=item["page_start"],
                page_end=item["page_end"],
                headings=tuple(item["headings"]),
                line_start=item["line_start"],
                line_end=item["line_end"],
            )
            for item in citations
        ),
        feedback=(
            ResponseFeedback(row["feedback"])
            if row["feedback"] is not None
            else None
        ),
        scope_metadata=DocumentMetadata(**scope_metadata),
    )
