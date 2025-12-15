"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/icons";
import { MetadataTagSelector } from "@/components/metadata-tag-selector";
import {
  type DiagnosticCitation,
  type DiagnosticSessionDetail,
  type DiagnosticSessionList,
  type DiagnosticSessionSummary,
  type DiagnosticTurn,
  type DocumentList,
  type DocumentMetadata,
  type DocumentRecord,
  type Health,
  type MetadataOptions,
  readJson,
  sourceLocation,
} from "@/lib/api";

const emptyMetadata: DocumentMetadata = { brand: [], machine: [], site: [], document_type: [] };

const actionLabels: Record<NonNullable<DiagnosticTurn["action"]>, string> = {
  ask_question: "Question",
  request_observation: "Observation needed",
  request_measurement: "Measurement needed",
  suggest_check: "Recommended check",
  answer_question: "Follow-up answer",
  report_diagnosis: "Diagnostic finding",
  escalate: "Escalation required",
  mark_resolved: "Fault resolved",
};

function DiagnosticMessage({ turn }: { turn: DiagnosticTurn }) {
  if (turn.role === "user") return <article className="user-message"><span>You</span><p>{turn.content}</p></article>;
  const citations = turn.payload.citations ?? [];
  return (
    <article className={`diagnostic-response diagnostic-response-${turn.action ?? "question"}`}>
      <header><span><Icon name={turn.action === "escalate" ? "shield" : "wrench"} /></span><div><small>Maintenance diagnostics</small><strong>{turn.action ? actionLabels[turn.action] : "Guidance"}</strong></div></header>
      <p className="diagnostic-response-copy">{turn.content.split(/(\[S\d+\])/g).map((part, index) => /^\[S\d+\]$/.test(part) ? <span className="inline-citation" key={`${part}-${index}`}>{part}</span> : part)}</p>
      {turn.payload.requires_safety_confirmation && <p className="diagnostic-safety-callout"><Icon name="shield" /> Confirm the equipment state and your authority before continuing.</p>}
      {citations.length > 0 && <div className="diagnostic-citations">{citations.map((citation: DiagnosticCitation) => <details key={`${turn.id}-${citation.source_id}`}><summary><span>{citation.source_id}</span><strong>{citation.document_title}</strong><small>{sourceLocation(citation)}</small></summary><p>{citation.excerpt}</p></details>)}</div>}
    </article>
  );
}

function SessionState({ session }: { session: DiagnosticSessionSummary | null }) {
  if (!session) return <aside className="diagnostic-state-panel diagnostic-state-empty"><Icon name="wrench" /><h2>Investigation state</h2><p>Known facts, checks and possible causes will appear here as the investigation develops.</p></aside>;
  const state = session.state;
  return (
    <aside className="diagnostic-state-panel">
      <div className="diagnostic-state-heading"><div><p className="eyebrow">Live case state</p><h2>Investigation</h2></div><span className={`diagnostic-status diagnostic-status-${session.status}`}>{session.status}</span></div>
      {state.summary && <p className="diagnostic-summary">{state.summary}</p>}
      <section><h3>Possible causes</h3>{state.hypotheses.length ? <div className="hypothesis-list">{state.hypotheses.map((item) => <article key={item.title}><span className={`likelihood likelihood-${item.likelihood}`}>{item.likelihood}</span><strong>{item.title}</strong><p>{item.rationale}</p>{item.contrary_observations.length > 0 && <small>Contrary: {item.contrary_observations.join("; ")}</small>}</article>)}</div> : <p className="empty-state-copy">No causes ranked yet.</p>}</section>
      <section><h3>Worker observations</h3>{state.observations.length ? <ul>{state.observations.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p className="empty-state-copy">Awaiting observations.</p>}</section>
      {state.measurements.length > 0 && <section><h3>Measurements</h3><dl className="diagnostic-measurements">{state.measurements.map((item, index) => <div key={`${item.name}-${index}`}><dt>{item.name}</dt><dd>{item.value}{item.unit ? ` ${item.unit}` : ""}</dd></div>)}</dl></section>}
      {state.completed_checks.length > 0 && <section><h3>Checks completed</h3><ul className="completed-checks">{state.completed_checks.map((item, index) => <li key={`${item}-${index}`}><Icon name="check" />{item}</li>)}</ul></section>}
      <footer><Icon name="shield" /><span><strong>Safety state</strong>{session.safety_status.replaceAll("_", " ")}</span></footer>
    </aside>
  );
}

export function DiagnosticWorkspace() {
  const [health, setHealth] = useState<Health | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [options, setOptions] = useState<MetadataOptions>(emptyMetadata);
  const [sessions, setSessions] = useState<DiagnosticSessionSummary[]>([]);
  const [detail, setDetail] = useState<DiagnosticSessionDetail | null>(null);
  const [message, setMessage] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [filters, setFilters] = useState<DocumentMetadata>(emptyMetadata);
  const [safetyStatus, setSafetyStatus] = useState<DiagnosticSessionSummary["safety_status"]>("unknown");
  const [loading, setLoading] = useState(false);
  const [initialising, setInitialising] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refreshSessions() {
    const response = await fetch("/api/backend/diagnostic-sessions?limit=50&offset=0", { cache: "no-store" });
    setSessions((await readJson<DiagnosticSessionList>(response)).items);
  }

  async function openSession(id: string) {
    setLoading(true); setError(null);
    try {
      const response = await fetch(`/api/backend/diagnostic-sessions/${id}`, { cache: "no-store" });
      const result = await readJson<DiagnosticSessionDetail>(response);
      setDetail(result); setDocumentId(result.session.document_id ?? ""); setFilters(result.session.metadata); setSafetyStatus(result.session.safety_status);
      window.history.replaceState({}, "", `/diagnostics?session=${encodeURIComponent(id)}`);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "The diagnostic session could not be opened."); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/backend/health", { cache: "no-store" }).then(readJson<Health>),
      fetch("/api/backend/documents?limit=100&lifecycle_status=current", { cache: "no-store" }).then(readJson<DocumentList>),
      fetch("/api/backend/metadata/options", { cache: "no-store" }).then(readJson<MetadataOptions>),
      fetch("/api/backend/diagnostic-sessions?limit=50&offset=0", { cache: "no-store" }).then(readJson<DiagnosticSessionList>),
    ]).then(([serviceHealth, documentList, metadataOptions, sessionList]) => {
      if (!active) return;
      setHealth(serviceHealth); setDocuments(documentList.items); setOptions(metadataOptions); setSessions(sessionList.items);
      const selected = new URLSearchParams(window.location.search).get("session");
      if (selected) void openSession(selected);
    }).catch((requestError: Error) => active && setError(requestError.message)).finally(() => active && setInitialising(false));
    return () => { active = false; };
  }, []);

  const ready = health?.diagnostics === "enabled";
  const selectedDocument = useMemo(() => documents.find((item) => item.id === documentId), [documentId, documents]);

  function selectDocument(value: string) {
    setDocumentId(value);
    const selected = documents.find((item) => item.id === value);
    if (selected) setFilters(selected.metadata);
  }

  function startNew() {
    setDetail(null); setMessage(""); setDocumentId(""); setFilters(emptyMetadata); setSafetyStatus("unknown"); setError(null);
    window.history.replaceState({}, "", "/diagnostics");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const prepared = message.trim();
    if (!prepared || loading || !ready) return;
    setLoading(true); setError(null);
    try {
      const continuing = Boolean(detail);
      const response = await fetch(continuing ? `/api/backend/diagnostic-sessions/${detail?.session.id}/turns` : "/api/backend/diagnostic-sessions", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(continuing ? { message: prepared, safety_status: safetyStatus } : {
          message: prepared, safety_status: safetyStatus,
          ...(documentId ? { document_id: documentId } : {}),
          ...Object.fromEntries(Object.entries(filters).filter(([, values]) => values.length)),
        }),
      });
      const result = await readJson<DiagnosticSessionDetail>(response);
      setDetail(result); setMessage(""); setSafetyStatus(result.session.safety_status);
      window.history.replaceState({}, "", `/diagnostics?session=${encodeURIComponent(result.session.id)}`);
      await refreshSessions();
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "The diagnostic step could not be completed."); }
    finally { setLoading(false); }
  }

  return (
    <div className="page page-diagnostics">
      <header className="page-header diagnostic-page-header"><div><p className="eyebrow">Guided maintenance</p><h1>Diagnose a fault</h1><p>Work through symptoms, observations and evidence one decision at a time.</p></div><div className="diagnostic-header-actions"><select aria-label="Open a diagnostic session" value={detail?.session.id ?? ""} onChange={(event) => event.target.value && void openSession(event.target.value)}><option value="">Recent investigations</option>{sessions.map((session) => <option value={session.id} key={session.id}>{session.title}</option>)}</select><button className="secondary-button" type="button" onClick={startNew}>New investigation</button></div></header>
      {!initialising && !ready && <div className="setup-guidance"><span><Icon name="settings" /></span><div><strong>Guided diagnostics needs an API key and embedded manuals</strong><p>Add the OpenAI key in Settings and ingest the relevant approved documentation.</p></div><Link href="/settings">View settings <Icon name="arrow" /></Link></div>}
      {error && <div className="toast-message toast-error" role="alert"><span>!</span>{error}</div>}
      <div className="diagnostic-layout">
        <main className="diagnostic-main">
          {detail?.turns.length ? <section className="diagnostic-transcript" aria-label="Diagnostic conversation">{detail.turns.map((turn) => <DiagnosticMessage turn={turn} key={turn.id} />)}</section> : <section className="diagnostic-intro"><span><Icon name="wrench" /></span><div><p className="eyebrow">Start with the symptom</p><h2>Describe what the machine is doing</h2><p>Include when the fault occurs, visible alarms, unusual noise, temperature or vibration, and anything that changed recently. The assistant will ask focused follow-up questions.</p></div></section>}
          <form className="diagnostic-entry" onSubmit={submit}>
            <label htmlFor="diagnostic-message">{detail ? "Observation, reading or follow-up question" : "Fault description"}</label>
            <textarea id="diagnostic-message" value={message} onChange={(event) => setMessage(event.target.value)} maxLength={2000} rows={4} placeholder={detail ? "For example: It trips only when the discharge valve is fully open." : "For example: The circulation pump starts normally, then trips after about five minutes."} />
            {!detail && <fieldset className="question-metadata-filters"><legend>Equipment scope <span>Recommended</span></legend>{([ ["brand", "Brand"], ["machine", "Machine"], ["site", "Site / area"], ["document_type", "Document type"] ] as const).map(([key, label]) => <MetadataTagSelector key={key} label={label} values={filters[key]} options={options[key]} allowCreate={false} onChange={(values) => setFilters({ ...filters, [key]: values })} />)}</fieldset>}
            <div className="diagnostic-entry-controls">
              {!detail && <label className="document-picker"><Icon name="manual" /><select aria-label="Search within a manual" value={documentId} onChange={(event) => selectDocument(event.target.value)}><option value="">All matching manuals</option>{documents.map((document) => <option value={document.id} key={document.id}>{document.title}</option>)}</select><Icon name="chevron" /></label>}
              <label className="safety-selector"><Icon name="shield" /><span>Equipment state</span><select aria-label="Equipment safety state" value={safetyStatus} onChange={(event) => setSafetyStatus(event.target.value as DiagnosticSessionSummary["safety_status"])}><option value="unknown">Not confirmed</option><option value="non_intrusive_only">Non-intrusive checks only</option><option value="confirmed_safe">Isolated and confirmed safe</option><option value="stop">Stop and escalate</option></select></label>
              <button className="primary-button" type="submit" disabled={!message.trim() || loading || !ready}>{loading ? <span className="button-spinner" /> : <Icon name="send" />}{loading ? "Assessing evidence" : detail ? "Continue diagnosis" : "Start diagnosis"}</button>
            </div>
            {selectedDocument && !detail && <p className="scope-note">Using <strong>{selectedDocument.title}</strong></p>}
          </form>
        </main>
        <SessionState session={detail?.session ?? null} />
      </div>
    </div>
  );
}
