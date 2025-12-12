from pathlib import Path
import sqlite3

import pytest

from maintenance_assistant.answering import GroundedAnswer
from maintenance_assistant.conversations import ConversationRole, ConversationStore
from maintenance_assistant.ingestion import IngestionError, LocalDocumentStore


def _answer(question: str, response: str = "Check the approved manual [S1].") -> GroundedAnswer:
    return GroundedAnswer(
        question=question,
        answerable=False,
        answer=response,
        citations=(),
        model="test-answer",
        input_tokens=14,
        output_tokens=6,
    )


def test_conversation_store_records_and_continues_complete_exchanges(
    tmp_path: Path,
) -> None:
    store = ConversationStore(LocalDocumentStore(tmp_path / "data"))

    first = store.record_exchange(
        _answer("How do I isolate the pump?"),
        scope_document_id="manual-1",
    )
    continued = store.record_exchange(
        _answer("What should I check afterwards?", "Inspect the seal [S1]."),
        conversation_id=first.conversation.id,
    )

    assert continued.conversation.title == "How do I isolate the pump?"
    assert continued.conversation.message_count == 4
    assert [message.role for message in continued.messages] == [
        ConversationRole.USER,
        ConversationRole.ASSISTANT,
        ConversationRole.USER,
        ConversationRole.ASSISTANT,
    ]
    assert [message.sequence for message in continued.messages] == [0, 1, 2, 3]
    assert continued.messages[0].scope_document_id == "manual-1"
    assert continued.messages[1].model == "test-answer"
    assert continued.messages[1].input_tokens == 14
    assert continued.messages[-1].content == "Inspect the seal [S1]."
    assert store.list_conversations() == (continued.conversation,)


def test_conversation_store_lists_recent_threads_and_deletes_messages(
    tmp_path: Path,
) -> None:
    store = ConversationStore(LocalDocumentStore(tmp_path / "data"))
    first = store.record_exchange(_answer("First question"))
    second = store.record_exchange(_answer("Second question"))

    assert [item.id for item in store.list_conversations(limit=1)] == [
        second.conversation.id
    ]
    assert store.list_conversations(limit=1, offset=1)[0].id == first.conversation.id
    assert store.delete_conversation(first.conversation.id) is True
    assert store.get_conversation(first.conversation.id) is None
    assert store.delete_conversation(first.conversation.id) is False


def test_conversation_store_rejects_missing_thread_and_invalid_input(
    tmp_path: Path,
) -> None:
    store = ConversationStore(LocalDocumentStore(tmp_path / "data"))

    with pytest.raises(KeyError):
        store.record_exchange(_answer("Question"), conversation_id="missing")
    with pytest.raises(ValueError, match="messages"):
        store.record_exchange(_answer("Question", " "))
    with pytest.raises(ValueError, match="limit"):
        store.list_conversations(limit=0)
    with pytest.raises(ValueError, match="offset"):
        store.list_conversations(offset=-1)

    assert store.list_conversations() == ()


def test_conversation_title_is_normalised_and_bounded(tmp_path: Path) -> None:
    store = ConversationStore(LocalDocumentStore(tmp_path / "data"))
    detail = store.record_exchange(_answer("  " + "pump " * 30))

    assert len(detail.conversation.title) == 80
    assert detail.conversation.title.endswith("…")
    assert "  " not in detail.conversation.title


def test_conversation_store_rolls_back_both_messages_when_one_write_fails(
    tmp_path: Path,
) -> None:
    document_store = LocalDocumentStore(tmp_path / "data")
    document_store.initialise()
    connection = sqlite3.connect(document_store.database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER reject_assistant_message
            BEFORE INSERT ON conversation_messages
            WHEN new.role = 'assistant'
            BEGIN
                SELECT RAISE(ABORT, 'test failure');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()
    store = ConversationStore(document_store)

    with pytest.raises(IngestionError, match="could not be saved"):
        store.record_exchange(_answer("Question"))

    assert store.list_conversations() == ()
