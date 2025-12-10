"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import {
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

export function AssistantWorkspace() {
  const [question, setQuestion] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [answer, setAnswer] = useState<GroundedAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [initialising, setInitialising] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/backend/health", { cache: "no-store" }).then(readJson<Health>),
      fetch("/api/backend/documents?limit=100", { cache: "no-store" }).then(readJson<DocumentList>),
    ])
      .then(([serviceHealth, documentList]) => {
        if (!active) return;
        setHealth(serviceHealth);
        setDocuments(documentList.items);
      })
      .catch((requestError: Error) => active && setError(requestError.message))
      .finally(() => active && setInitialising(false));
    return () => { active = false; };
  }, []);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === documentId),
    [documentId, documents],
  );
  const ready = health?.answers === "enabled";

  async function submitQuestion(event: FormEvent) {
    event.preventDefault();
    const prepared = question.trim();
    if (!prepared || loading) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      const response = await fetch("/api/backend/answers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: prepared,
          max_sources: 5,
          ...(documentId ? { document_id: documentId } : {}),
        }),
      });
      setAnswer(await readJson<GroundedAnswer>(response));
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
          <form className="question-card" onSubmit={submitQuestion}>
            <div className="question-card-heading">
              <span className="question-icon"><Icon name="spark" /></span>
              <div><h2>What do you need help with?</h2><p>Describe the equipment, symptom or task in everyday language.</p></div>
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
                {loading ? "Checking manuals" : "Ask assistant"}
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

          {!answer && !loading && !error && (
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

          {answer && (
            <section className="answer-card" aria-live="polite">
              <div className="answer-heading">
                <span className="answer-symbol"><Icon name={answer.answerable ? "spark" : "manual"} /></span>
                <div><p className="eyebrow">{answer.answerable ? "Grounded answer" : "More information needed"}</p><h2>{answer.answerable ? "Based on your manuals" : "No supported answer found"}</h2></div>
                {answer.answerable && <span className="verified-badge"><Icon name="check" /> Sources verified</span>}
              </div>
              <AnswerText text={answer.answer} />
              {answer.citations.length > 0 && (
                <div className="sources-block">
                  <div className="sources-heading"><h3>Sources used</h3><span>{answer.citations.length} {answer.citations.length === 1 ? "source" : "sources"}</span></div>
                  <div className="source-list">
                    {answer.citations.map((citation) => (
                      <details className="source-card" key={citation.source_id}>
                        <summary>
                          <span className="source-id">{citation.source_id}</span>
                          <span className="source-title"><strong>{citation.document.title}</strong><small>{sourceLocation(citation)}</small></span>
                          <span className="source-score">{Math.round(citation.score * 100)}% match</span>
                          <Icon name="chevron" />
                        </summary>
                        <div className="source-excerpt"><p>{citation.excerpt}</p><span>From {citation.document.original_filename}</span></div>
                      </details>
                    ))}
                  </div>
                </div>
              )}
              <p className="safety-note"><Icon name="shield" /> Confirm critical steps against the approved manual and your site safety procedures.</p>
            </section>
          )}
        </div>

        <aside className="assistant-sidebar-panel">
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
