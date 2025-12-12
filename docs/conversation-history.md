# Conversation history

## Purpose

The maintenance workspace retains completed conversations so a worker can
return to an earlier question, inspect the same cited evidence and continue the
thread without relying on one browser tab remaining open.

History is server-owned local data. It is not stored in browser `localStorage`
and does not require an additional external service.

## What is stored

SQLite schema version 8 includes a conversation record, an ordered message
ledger and response feedback. Each successful answer request stores, in one transaction:

- the worker's exact normalised question;
- the assistant's validated response;
- user or assistant role and stable sequence number;
- creation and last-updated timestamps;
- the optional manual scope used for that question;
- answerability, provider model and input/output token counts; and
- a snapshot of every validated citation, including the source document name,
  excerpt and page, heading or line location.

Citation snapshots deliberately remain readable if a source manual is later
archived, superseded or permanently deleted. They record what supported the
answer at the time; they do not make an old answer current or authoritative.

The first question becomes the conversation title, normalised and limited to 80
characters. Later questions append ordered user/assistant message pairs without
changing that title.

## Atomicity and failures

Answer generation and citation validation finish before history is written.
The user question and assistant response are then committed together. A provider
failure, invalid model response or failed grounding check therefore does not
leave a user-only or assistant-only entry.

Continuing a missing or deleted conversation returns
`conversation_not_found` before invoking the answer provider. Concurrent
appends use an immediate SQLite transaction so message sequence numbers remain
unique and ordered.

## API lifecycle

- `POST /answers` creates a conversation when `conversation_id` is omitted.
- Supplying the returned `conversation_id` appends a follow-up exchange.
- `GET /conversations` lists threads by most recent activity with pagination.
- `GET /conversations/{id}` returns every ordered message and citation.
- `DELETE /conversations/{id}` permanently removes the thread and all messages.
- `PUT /conversations/{id}/messages/{message_id}/feedback` records or changes a
  thumbs-up or thumbs-down rating for an assistant response.
- `DELETE /conversations/{id}/messages/{message_id}/feedback` clears that rating.

Conversation deletion does not delete manuals, chunks or vectors. Manual
deletion does not erase citation snapshots retained in an earlier conversation.
Feedback is limited to assistant messages, has foreign keys to both its message
and conversation, and is removed automatically when the conversation is deleted.

## Context boundary

Saved history is not automatically sent to OpenAI and is not treated as
retrieval evidence. Every follow-up searches the current manual collection using
only the new question, then receives freshly validated citations. This prevents
an earlier generated answer from silently grounding a later one.

Adding true conversational model context would be a separate feature requiring
explicit token limits, prompt-injection handling and rules for distinguishing
conversation statements from approved manual evidence.

## Privacy and retention

Conversation content remains in the configured local data directory and Docker
volume. Anyone with filesystem access to that directory can read the SQLite
database, so normal workstation permissions and backups must protect it. The
initial local-first version does not encrypt individual database fields and has
no per-user accounts.

Workers can delete individual conversations from the interface after an
explicit confirmation. A future multi-user deployment should add authentication,
authorisation, retention policy controls and audit requirements before sharing
one history database across teams.
