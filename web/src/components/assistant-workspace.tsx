"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import {
  type ConversationDetail,
  type ConversationList,
  type ConversationMessage,
  type ConversationSummary,
  type DocumentList,
  type DocumentRecord,
  type GroundedAnswer,
  type Health,
  readJson,
  sourceLocation,
} from "@/lib/api";

const suggestions = [
  "What checks should I complete before starting the pump?",
  "How do I safely isolate equipment before maintenance?",
  "What does the manual say about abnormal vibration?",
];

function AnswerText({ text }: { text: string }) {
  return (
    <p className="answer-copy">
      {text.split(/(\[S\d+\])/g).map((part, index) =>
        /^\[S\d+\]$/.test(part) ? <span className="inline-citation" key={`${part}-${index}`}>{part}</span> : part,
      )}
    </p>
  );
}

function StoredAnswer({ message }: { message: ConversationMessage }) {
  const answerable = message.answerable !== false;
  return (
    <section className="answer-card stored-answer">
      <div className="answer-heading">
        <span className="answer-symbol"><Icon name={answerable ? "spark" : "manual"} /></span>
        <div><p className="eyebrow">{answerable ? "Grounded answer" : "More information needed"}</p><h2>{answerable ? "Based on your manuals" : "No supported answer found"}</h2></div>
        {answerable && message.citations.length > 0 && <span className="verified-badge"><Icon name="check" /> Sources verified</span>}
      </div>
      <AnswerText text={message.content} />
      {message.citations.length > 0 && (
        <div className="sources-block">
          <div className="sources-heading"><h3>Sources used</h3><span>{message.citations.length} {message.citations.length === 1 ? "source" : "sources"}</span></div>
          <div className="source-list">
            {message.citations.map((citation) => (
              <details className="source-card" key={`${message.id}-${citation.source_id}`}>
                <summary>
                  <span className="source-id">{citation.source_id}</span>
                  <span className="source-title"><strong>{citation.document_title}</strong><small>{sourceLocation(citation)}</small></span>
                  <span className="source-score">{Math.round(citation.score * 100)}% match</span>
                  <Icon name="chevron" />
                </summary>
                <div className="source-excerpt"><p>{citation.excerpt}</p><span>From {citation.original_filename}</span></div>
              </details>
            ))}
          </div>
        </div>
      )}
      <p className="safety-note"><Icon name="shield" /> Confirm critical steps against the approved manual and your site safety procedures.</p>
    </section>
  );
}

function historyDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(new Date(value));
}

export function AssistantWorkspace() {
  const [question, setQuestion] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [hasOlderConversations, setHasOlderConversations] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [initialising, setInitialising] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/backend/health", { cache: "no-store" }).then(readJson<Health>),
      fetch("/api/backend/documents?limit=100&lifecycle_status=current", { cache: "no-store" }).then(readJson<DocumentList>),
      fetch("/api/backend/conversations?limit=50", { cache: "no-store" }).then(readJson<ConversationList>),
    ])
      .then(([serviceHealth, documentList, conversationList]) => {
        if (!active) return;
        setHealth(serviceHealth);
        setDocuments(documentList.items);
        setConversations(conversationList.items);
        setHasOlderConversations(conversationList.items.length === 50);
      })
      .catch((requestError: Error) => active && setError(requestError.message))
      .finally(() => active && setInitialising(false));
    return () => { active = false; };
  }, []);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === documentId),
    [documentId, documents],
  );
  const activeConversation = conversations.find((item) => item.id === conversationId);
  const ready = health?.answers === "enabled";

  async function refreshConversations() {
    const response = await fetch("/api/backend/conversations?limit=50", { cache: "no-store" });
    const list = await readJson<ConversationList>(response);
    setConversations(list.items);
    setHasOlderConversations(list.items.length === 50);
  }

  async function loadOlderConversations() {
    setHistoryLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/backend/conversations?limit=50&offset=${conversations.length}`,
        { cache: "no-store" },
      );
      const list = await readJson<ConversationList>(response);
      setConversations((current) => [...current, ...list.items]);
      setHasOlderConversations(list.items.length === 50);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Older conversations could not be loaded.");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function openConversation(id: string) {
    setHistoryLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/backend/conversations/${id}`, { cache: "no-store" });
      const detail = await readJson<ConversationDetail>(response);
      setConversationId(detail.conversation.id);
      setMessages(detail.messages);
      const previousScope = detail.messages.at(-1)?.scope_document_id;
      setDocumentId(
        previousScope && documents.some((document) => document.id === previousScope)
          ? previousScope
          : "",
      );
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Conversation history could not be opened.");
    } finally {
      setHistoryLoading(false);
    }
  }

  function startConversation() {
    setConversationId(null);
    setMessages([]);
    setQuestion("");
    setDocumentId("");
    setError(null);
  }

  async function deleteConversation(conversation: ConversationSummary) {
    if (!window.confirm(`Delete “${conversation.title}” and all of its messages?`)) return;
    setError(null);
    try {
      const response = await fetch(`/api/backend/conversations/${conversation.id}`, { method: "DELETE" });
      if (!response.ok) await readJson(response);
      if (conversationId === conversation.id) startConversation();
      await refreshConversations();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Conversation history could not be deleted.");
    }
  }

  async function submitQuestion(event: FormEvent) {
    event.preventDefault();
    const prepared = question.trim();
    if (!prepared || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/backend/answers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: prepared,
          max_sources: 5,
          ...(documentId ? { document_id: documentId } : {}),
          ...(conversationId ? { conversation_id: conversationId } : {}),
        }),
      });
      const result = await readJson<GroundedAnswer>(response);
      setQuestion("");
      await Promise.all([openConversation(result.conversation_id), refreshConversations()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The question could not be answered.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page page-assistant">
      <header className="page-header assistant-header">
        <div>
          <p className="eyebrow">Maintenance workspace</p>
          <h1>Ask your manuals</h1>
          <p>Get clear guidance grounded in the documents your team trusts.</p>
        </div>
        <div className={`readiness-pill ${ready ? "readiness-ready" : "readiness-setup"}`}>
          <span className="status-dot" />
          {initialising ? "Checking knowledge base" : ready ? "Knowledge base ready" : "Setup required"}
        </div>
      </header>

      <section className="assistant-grid">
        <div className="assistant-primary">
          {activeConversation && <div className="conversation-heading"><span><Icon name="history" /></span><div><p>Current conversation</p><strong>{activeConversation.title}</strong></div><button type="button" onClick={startConversation}>New conversation</button></div>}

          {messages.length > 0 && (
            <section className="conversation-transcript" aria-label="Conversation messages">
              {messages.map((message) => message.role === "user" ? (
                <article className="user-message" key={message.id}><span>You</span><p>{message.content}</p></article>
              ) : <StoredAnswer message={message} key={message.id} />)}
            </section>
          )}

          {historyLoading && <div className="history-loading"><span className="button-spinner dark" /> Opening conversation…</div>}

          <form className={`question-card ${messages.length ? "question-card-follow-up" : ""}`} onSubmit={submitQuestion}>
            <div className="question-card-heading">
              <span className="question-icon"><Icon name="spark" /></span>
              <div><h2>{messages.length ? "Ask a follow-up" : "What do you need help with?"}</h2><p>{messages.length ? "Continue this saved conversation with another grounded question." : "Describe the equipment, symptom or task in everyday language."}</p></div>
            </div>
            <label className="sr-only" htmlFor="maintenance-question">Maintenance question</label>
            <textarea
              id="maintenance-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="For example: What should I check if the pump is making a rattling noise?"
              rows={4}
              maxLength={2000}
            />
            <div className="question-actions">
              <label className="document-picker">
                <span className="sr-only">Search within a manual</span>
                <Icon name="manual" />
                <select value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
                  <option value="">All manuals</option>
                  {documents.map((document) => <option value={document.id} key={document.id}>{document.title}</option>)}
                </select>
                <Icon name="chevron" />
              </label>
              <button className="primary-button ask-button" type="submit" disabled={!question.trim() || loading || !ready}>
                {loading ? <span className="button-spinner" /> : <Icon name="send" />}
                {loading ? "Checking manuals" : messages.length ? "Send follow-up" : "Ask assistant"}
              </button>
            </div>
            {selectedDocument && <p className="scope-note">Searching only <strong>{selectedDocument.title}</strong></p>}
          </form>

          {!initialising && !ready && !error && (
            <div className="setup-guidance">
              <span><Icon name="settings" /></span>
              <div><strong>The assistant needs to be connected</strong><p>Manuals can still be managed, but grounded answers require the providers shown in Settings.</p></div>
              <Link href="/settings">View settings <Icon name="arrow" /></Link>
            </div>
          )}

          {!messages.length && !loading && !error && (
            <section className="starter-section" aria-labelledby="starter-heading">
              <div className="section-heading-row"><div><p className="eyebrow">Quick start</p><h2 id="starter-heading">Common questions</h2></div><span>Choose one to edit</span></div>
              <div className="suggestion-grid">
                {suggestions.map((suggestion, index) => (
                  <button className="suggestion-card" type="button" onClick={() => setQuestion(suggestion)} key={suggestion}>
                    <span className="suggestion-number">0{index + 1}</span>
                    <strong>{suggestion}</strong>
                    <span className="suggestion-arrow"><Icon name="arrow" /></span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {loading && (
            <section className="answer-card answer-loading" aria-live="polite">
              <div className="answer-heading"><span className="answer-symbol"><Icon name="spark" /></span><div><span className="skeleton skeleton-short" /><span className="skeleton skeleton-tiny" /></div></div>
              <div className="skeleton" /><div className="skeleton" /><div className="skeleton skeleton-medium" />
              <p><span className="button-spinner dark" /> Finding the strongest evidence…</p>
            </section>
          )}

          {error && (
            <section className="message-card message-error" role="alert">
              <span className="message-icon">!</span>
              <div><h2>We couldn’t complete that request</h2><p>{error}</p>{!ready && <Link href="/settings">Open Settings</Link>}</div>
            </section>
          )}
        </div>

        <aside className="assistant-sidebar-panel">
          <article className="conversation-history-card">
            <div className="history-card-heading"><div><span><Icon name="history" /></span><div><p className="eyebrow">Saved locally</p><h2>Conversation history</h2></div></div><button type="button" onClick={startConversation}>New</button></div>
            {initialising ? <p className="history-empty">Loading history…</p> : conversations.length ? (
              <div className="history-list">
                {conversations.map((conversation) => (
                  <div className={`history-item ${conversation.id === conversationId ? "history-item-active" : ""}`} key={conversation.id}>
                    <button className="history-open" type="button" onClick={() => openConversation(conversation.id)} disabled={historyLoading}>
                      <strong>{conversation.title}</strong>
                      <span>{historyDate(conversation.updated_at)} · {conversation.message_count} messages</span>
                    </button>
                    <button className="history-delete" type="button" aria-label={`Delete ${conversation.title}`} onClick={() => deleteConversation(conversation)}><Icon name="trash" /></button>
                  </div>
                ))}
                {hasOlderConversations && <button className="history-more" type="button" onClick={loadOlderConversations} disabled={historyLoading}>Load older conversations</button>}
              </div>
            ) : <p className="history-empty">Your completed conversations will appear here.</p>}
          </article>
          <div className="insight-card library-insight">
            <div className="insight-top"><span className="insight-icon"><Icon name="manual" /></span><Link href="/manuals">Manage</Link></div>
            <strong className="insight-number">{initialising ? "—" : documents.length}</strong>
            <h2>{documents.length === 1 ? "manual available" : "manuals available"}</h2>
            <p>{documents.length ? "Your answers can draw from every indexed section." : "Add a manual to start building your knowledge base."}</p>
          </div>
          <div className="insight-card trust-insight">
            <div className="trust-illustration"><span><Icon name="shield" /></span><i /><i /><i /></div>
            <p className="eyebrow">Designed for trust</p>
            <h2>See the evidence behind every answer</h2>
            <p>Open each source to compare the guidance with the exact manual excerpt.</p>
          </div>
          <div className="help-strip"><span>Need better results?</span><Link href="/manuals">Add an updated manual <Icon name="arrow" /></Link></div>
        </aside>
      </section>
    </div>
  );
}
