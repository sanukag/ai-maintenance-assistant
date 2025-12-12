"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import {
  type ConversationDetail,
  type ConversationMessage,
  type DocumentList,
  type DocumentRecord,
  type GroundedAnswer,
  type Health,
  type MetadataOptions,
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

function StoredAnswer({
  message,
  feedbackLoading,
  onFeedback,
}: {
  message: ConversationMessage;
  feedbackLoading: boolean;
  onFeedback: (message: ConversationMessage, rating: "up" | "down") => void;
}) {
  const answerable = message.answerable !== false;
  return (
    <section className="answer-card stored-answer">
      <div className="answer-heading">
        <span className="answer-symbol"><Icon name={answerable ? "spark" : "manual"} /></span>
        <div><p className="eyebrow">{answerable ? "Grounded answer" : "More information needed"}</p><h2>{answerable ? "Based on your manuals" : "No supported answer found"}</h2></div>
        {answerable && message.citations.length > 0 && <span className="verified-badge">{message.citations.length} {message.citations.length === 1 ? "citation" : "citations"}</span>}
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
      <div className="answer-feedback">
        <span>Was this response helpful?</span>
        <div>
          <button type="button" aria-label="Mark response as helpful" aria-pressed={message.feedback === "up"} disabled={feedbackLoading} onClick={() => onFeedback(message, "up")}><Icon name="thumb-up" /></button>
          <button type="button" aria-label="Mark response as unhelpful" aria-pressed={message.feedback === "down"} disabled={feedbackLoading} onClick={() => onFeedback(message, "down")}><Icon name="thumb-down" /></button>
        </div>
      </div>
    </section>
  );
}

export function AssistantWorkspace() {
  const [question, setQuestion] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [brand, setBrand] = useState("");
  const [machine, setMachine] = useState("");
  const [site, setSite] = useState("");
  const [documentType, setDocumentType] = useState("");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [metadataOptions, setMetadataOptions] = useState<MetadataOptions>({ brand: [], machine: [], site: [], document_type: [] });
  const [health, setHealth] = useState<Health | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [feedbackLoadingId, setFeedbackLoadingId] = useState<string | null>(null);
  const [initialising, setInitialising] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const openConversation = useCallback(async (id: string) => {
    setHistoryLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/backend/conversations/${id}`, { cache: "no-store" });
      const detail = await readJson<ConversationDetail>(response);
      setConversationId(detail.conversation.id);
      setConversationTitle(detail.conversation.title);
      setMessages(detail.messages);
      setDocumentId(detail.messages.at(-1)?.scope_document_id ?? "");
      const previousMetadata = detail.messages.at(-1)?.scope_metadata;
      setBrand(previousMetadata?.brand[0] ?? "");
      setMachine(previousMetadata?.machine[0] ?? "");
      setSite(previousMetadata?.site[0] ?? "");
      setDocumentType(previousMetadata?.document_type[0] ?? "");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Conversation history could not be opened.");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const startConversation = useCallback(() => {
    setConversationId(null);
    setConversationTitle(null);
    setMessages([]);
    setQuestion("");
    setDocumentId("");
    setBrand("");
    setMachine("");
    setSite("");
    setDocumentType("");
    setError(null);
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/backend/health", { cache: "no-store" }).then(readJson<Health>),
      fetch("/api/backend/documents?limit=100&lifecycle_status=current", { cache: "no-store" }).then(readJson<DocumentList>),
      fetch("/api/backend/metadata/options", { cache: "no-store" }).then(readJson<MetadataOptions>),
    ])
      .then(([serviceHealth, documentList, availableMetadata]) => {
        if (!active) return;
        setHealth(serviceHealth);
        setDocuments(documentList.items);
        setMetadataOptions(availableMetadata);
        const selected = new URLSearchParams(window.location.search).get("conversation");
        if (selected) void openConversation(selected);
      })
      .catch((requestError: Error) => active && setError(requestError.message))
      .finally(() => active && setInitialising(false));
    return () => { active = false; };
  }, [openConversation]);

  useEffect(() => {
    const selectConversation = (event: Event) => {
      const selected = (event as CustomEvent<{ conversationId: string | null }>).detail.conversationId;
      if (selected) void openConversation(selected);
      else startConversation();
    };
    window.addEventListener("assistant-conversation-selected", selectConversation);
    return () => window.removeEventListener("assistant-conversation-selected", selectConversation);
  }, [openConversation, startConversation]);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === documentId),
    [documentId, documents],
  );
  const ready = health?.answers === "enabled";
  const brands = metadataOptions.brand;
  const machines = metadataOptions.machine;
  const sites = metadataOptions.site;
  const documentTypes = metadataOptions.document_type;

  function selectDocument(value: string) {
    setDocumentId(value);
    const document = documents.find((item) => item.id === value);
    if (!document) return;
    setBrand(document.metadata.brand[0] ?? "");
    setMachine(document.metadata.machine[0] ?? "");
    setSite(document.metadata.site[0] ?? "");
    setDocumentType(document.metadata.document_type[0] ?? "");
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
          ...(brand ? { brand: [brand] } : {}),
          ...(machine ? { machine: [machine] } : {}),
          ...(site ? { site: [site] } : {}),
          ...(documentType ? { document_type: [documentType] } : {}),
          ...(conversationId ? { conversation_id: conversationId } : {}),
        }),
      });
      const result = await readJson<GroundedAnswer>(response);
      setQuestion("");
      window.history.replaceState({}, "", `/?conversation=${encodeURIComponent(result.conversation_id)}`);
      await openConversation(result.conversation_id);
      window.dispatchEvent(new CustomEvent("conversation-history-updated"));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The question could not be answered.");
    } finally {
      setLoading(false);
    }
  }

  async function submitFeedback(message: ConversationMessage, rating: "up" | "down") {
    if (!conversationId || feedbackLoadingId) return;
    const clearing = message.feedback === rating;
    setFeedbackLoadingId(message.id);
    setError(null);
    try {
      const response = await fetch(
        `/api/backend/conversations/${conversationId}/messages/${message.id}/feedback`,
        clearing
          ? { method: "DELETE" }
          : {
              method: "PUT",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ rating }),
            },
      );
      if (!response.ok) await readJson(response);
      setMessages((current) => current.map((item) => (
        item.id === message.id ? { ...item, feedback: clearing ? null : rating } : item
      )));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Response feedback could not be saved.");
    } finally {
      setFeedbackLoadingId(null);
    }
  }

  return (
    <div className="page page-assistant">
      <header className="page-header assistant-header">
        <div>
          <p className="eyebrow">Maintenance workspace</p>
          <h1>Ask your manuals</h1>
          <p>Search approved maintenance information and continue previous work.</p>
        </div>
        <div className={`readiness-pill ${ready ? "readiness-ready" : "readiness-setup"}`}>
          <span className="status-dot" />
          {initialising ? "Checking knowledge base" : ready ? "Knowledge base ready" : "Setup required"}
        </div>
      </header>

      <section className="assistant-grid">
        <div className="assistant-primary">
          {conversationTitle && <div className="conversation-heading"><span><Icon name="history" /></span><div><p>Current conversation</p><strong>{conversationTitle}</strong></div><button type="button" onClick={() => window.dispatchEvent(new CustomEvent("assistant-conversation-selected", { detail: { conversationId: null } }))}>New conversation</button></div>}

          {messages.length > 0 && (
            <section className="conversation-transcript" aria-label="Conversation messages">
              {messages.map((message) => message.role === "user" ? (
                <article className="user-message" key={message.id}><span>You</span><p>{message.content}</p></article>
              ) : <StoredAnswer message={message} feedbackLoading={feedbackLoadingId === message.id} onFeedback={submitFeedback} key={message.id} />)}
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
            <fieldset className="question-metadata-filters">
              <legend>Equipment filters <span>Optional</span></legend>
              <label>Brand<select aria-label="Filter by brand" value={brand} onChange={(event) => setBrand(event.target.value)}><option value="">All brands</option>{brands.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
              <label>Machine<select aria-label="Filter by machine" value={machine} onChange={(event) => setMachine(event.target.value)}><option value="">All machines</option>{machines.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
              <label>Site / area<select aria-label="Filter by site or area" value={site} onChange={(event) => setSite(event.target.value)}><option value="">All sites</option>{sites.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
              <label>Document type<select aria-label="Filter by document type" value={documentType} onChange={(event) => setDocumentType(event.target.value)}><option value="">All types</option>{documentTypes.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
            </fieldset>
            <div className="question-actions">
              <label className="document-picker">
                <span className="sr-only">Search within a manual</span>
                <Icon name="manual" />
                <select value={documentId} onChange={(event) => selectDocument(event.target.value)}>
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
      </section>
    </div>
  );
}
