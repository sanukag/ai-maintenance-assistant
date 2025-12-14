"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { type CredentialList, type CredentialStatus, type Health, type RuntimeMetrics, readJson } from "@/lib/api";

type StatusRow = { label: string; value: string; detail: string; available: boolean };

export function SettingsPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [metrics, setMetrics] = useState<RuntimeMetrics | null>(null);
  const [credentials, setCredentials] = useState<CredentialStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingCredential, setSavingCredential] = useState(false);
  const [editingCredential, setEditingCredential] = useState<string | null>(null);
  const [credentialValue, setCredentialValue] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthResponse, metricsResponse, credentialsResponse] = await Promise.all([
        fetch("/api/backend/health", { cache: "no-store" }),
        fetch("/api/backend/metrics", { cache: "no-store" }),
        fetch("/api/backend/credentials", { cache: "no-store" }),
      ]);
      setHealth(await readJson<Health>(healthResponse));
      setMetrics(await readJson<RuntimeMetrics>(metricsResponse));
      setCredentials((await readJson<CredentialList>(credentialsResponse)).items);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Service status could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch("/api/backend/health", { cache: "no-store" }).then(readJson<Health>),
      fetch("/api/backend/metrics", { cache: "no-store" }).then(readJson<RuntimeMetrics>),
      fetch("/api/backend/credentials", { cache: "no-store" }).then(readJson<CredentialList>),
    ])
      .then(([healthResult, metricsResult, credentialResult]) => { if (active) { setHealth(healthResult); setMetrics(metricsResult); setCredentials(credentialResult.items); } })
      .catch((requestError: Error) => { if (active) setError(requestError.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const beginCredentialEdit = (credential: CredentialStatus) => {
    setCredentialValue("");
    setEditingCredential(credential.name);
    setNotice(null);
    setError(null);
  };

  const saveCredential = async (credential: CredentialStatus) => {
    setSavingCredential(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/backend/credentials/${credential.name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: credentialValue }),
      });
      await readJson<CredentialStatus>(response);
      setCredentialValue("");
      setEditingCredential(null);
      setNotice(`${credential.label} saved. External AI services are ready to use.`);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The API key could not be saved.");
    } finally {
      setSavingCredential(false);
    }
  };

  const deleteCredential = async (credential: CredentialStatus) => {
    if (!window.confirm(`Delete the saved ${credential.label}? External AI features will be disabled unless the service environment supplies a key.`)) return;
    setSavingCredential(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/backend/credentials/${credential.name}`, { method: "DELETE" });
      await readJson<CredentialStatus>(response);
      setEditingCredential(null);
      setCredentialValue("");
      setNotice(`${credential.label} deleted.`);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The saved API key could not be deleted.");
    } finally {
      setSavingCredential(false);
    }
  };

  const rows: StatusRow[] = [
    { label: "Document storage", value: health?.storage === "ok" ? "Online" : "Unavailable", detail: "SQLite and local document files", available: health?.storage === "ok" },
    { label: "OCR", value: health?.ocr === "available" ? "Available" : health?.ocr === "disabled" ? "Disabled" : "Unavailable", detail: health?.ocr_engine ? `${health.ocr_engine}${health.ocr_version ? ` ${health.ocr_version}` : ""}` : "No engine configured", available: health?.ocr === "available" },
    { label: "Visual analysis", value: health?.visual_analysis === "available" ? "Available" : health?.visual_analysis === "disabled" ? "Disabled" : "Unavailable", detail: health?.visual_analysis_model ?? "No model configured", available: health?.visual_analysis === "available" },
    { label: "Embeddings", value: health?.embeddings === "enabled" ? "Enabled" : "Disabled", detail: health?.embedding_model ?? "No model configured", available: health?.embeddings === "enabled" },
    { label: "Vector index", value: health?.vector_index === "available" ? "Available" : health?.vector_index === "disabled" ? "SQLite mode" : "Fallback active", detail: health?.vector_store === "qdrant" ? "Qdrant HNSW index with SQLite fallback" : "SQLite cosine search", available: health?.vector_index !== "unavailable" },
    { label: "Evidence reranking", value: health?.reranking === "enabled" ? "Enabled" : "Disabled", detail: health?.rerank_model ?? "Fused retrieval order", available: health?.reranking === "enabled" },
    { label: "Answer generation", value: health?.answers === "enabled" ? "Enabled" : "Disabled", detail: health?.answer_model ?? "No model configured", available: health?.answers === "enabled" },
  ];

  return (
    <div className="page page-settings">
      <header className="page-header">
        <div><p className="eyebrow">Administration</p><h1>Settings</h1><p>Runtime status, configuration reference and developer information.</p></div>
        <button className="secondary-button" type="button" onClick={refresh} disabled={loading}><Icon name="refresh" className={loading ? "spinning" : ""} /> Refresh status</button>
      </header>

      {error && <div className="toast-message toast-error" role="alert"><span>!</span>{error}</div>}
      {notice && <div className="toast-message" role="status"><span><Icon name="check" /></span>{notice}</div>}

      <div className="enterprise-settings-layout">
        <section className="settings-status-panel" aria-labelledby="service-status-heading">
          <div className="panel-heading"><div><p className="eyebrow">System</p><h2 id="service-status-heading">Service status</h2></div><span className={`inline-state ${health?.status === "ok" ? "state-on" : "state-off"}`}><i />{loading ? "Checking" : health?.status === "ok" ? "API connected" : "Attention required"}</span></div>
          <div className="status-table">
            {rows.map((row) => (
              <div className="status-table-row" key={row.label}>
                <strong>{row.label}</strong><span>{row.detail}</span><b className={row.available ? "status-value-on" : "status-value-off"}><i />{loading ? "Checking" : row.value}</b>
              </div>
            ))}
          </div>
        </section>

        <section className="credential-settings-card" aria-labelledby="credential-settings-heading">
          <div className="panel-heading">
            <div><p className="eyebrow">External services</p><h2 id="credential-settings-heading">API keys</h2></div>
            <span className="credential-security-note"><Icon name="shield" /> Encrypted locally</span>
          </div>
          <p className="credential-intro">Add the API key only. Service providers and models are fixed by the application and cannot be changed here. Saving a key enables the listed capabilities immediately and on future launches.</p>
          <div className="credential-list">
            {credentials.map((credential) => (
              <article className="credential-item" key={credential.name}>
                <div className="credential-item-heading">
                  <div><strong>{credential.label}</strong><p>{credential.description}</p></div>
                  <span className={`inline-state ${credential.configured ? "state-on" : "state-off"}`}><i />{credential.configured ? "Configured" : "Required"}</span>
                </div>
                <div className="credential-capabilities" aria-label="Used by">
                  {credential.used_by.map((capability) => <span key={capability}>{capability}</span>)}
                </div>
                {credential.configured && editingCredential !== credential.name && (
                  <div className="credential-summary">
                    <div><small>{credential.source === "saved" ? "Saved in the local data volume" : "Supplied by the service environment"}</small><code>{credential.masked_value}</code></div>
                    <div className="credential-actions">
                      <button className="secondary-button" type="button" onClick={() => beginCredentialEdit(credential)}>{credential.source === "saved" ? "Edit key" : "Replace locally"}</button>
                      {credential.can_delete && <button className="credential-delete-button" type="button" onClick={() => deleteCredential(credential)} disabled={savingCredential}>Delete</button>}
                    </div>
                  </div>
                )}
                {!credential.configured && editingCredential !== credential.name && (
                  <button className="primary-button credential-add-button" type="button" onClick={() => beginCredentialEdit(credential)}>Add API key</button>
                )}
                {editingCredential === credential.name && (
                  <form className="credential-form" onSubmit={(event) => { event.preventDefault(); void saveCredential(credential); }}>
                    <label htmlFor={`credential-${credential.name}`}>API key</label>
                    <input id={`credential-${credential.name}`} type="password" value={credentialValue} onChange={(event) => setCredentialValue(event.target.value)} placeholder="Paste API key" autoComplete="new-password" autoFocus />
                    <p>The complete key is sent only to the local API and is never shown again.</p>
                    <div>
                      <button className="secondary-button" type="button" onClick={() => { setEditingCredential(null); setCredentialValue(""); }} disabled={savingCredential}>Cancel</button>
                      <button className="primary-button" type="submit" disabled={savingCredential || credentialValue.trim().length < 8}>{savingCredential ? "Saving…" : "Save API key"}</button>
                    </div>
                  </form>
                )}
              </article>
            ))}
          </div>
        </section>

        <section className="developer-card">
          <div className="developer-heading"><span><Icon name="database" /></span><div><p className="eyebrow">Performance</p><h2>Local runtime</h2></div></div>
          <dl className="runtime-list">
            <div><dt>API requests</dt><dd>{metrics?.requests_total ?? "—"}</dd></div>
            <div><dt>Server errors</dt><dd>{metrics?.errors_total ?? "—"}</dd></div>
            <div><dt>Embedding cache</dt><dd>{metrics ? `${metrics.embedding_cache.entries.toLocaleString()} entries · ${metrics.embedding_cache.hits.toLocaleString()} hits` : "—"}</dd></div>
            <div><dt>SQLite journal</dt><dd><code>{metrics?.sqlite.journal_mode.toUpperCase() ?? "—"}</code></dd></div>
            <div><dt>Busy timeout</dt><dd>{metrics ? `${metrics.sqlite.busy_timeout_ms.toLocaleString()} ms` : "—"}</dd></div>
          </dl>
        </section>

        <section className="developer-card">
          <div className="developer-heading"><span><Icon name="server" /></span><div><p className="eyebrow">Developer information</p><h2>Runtime reference</h2></div></div>
          <dl className="runtime-list">
            <div><dt>Web application</dt><dd>Next.js 16 · App Router</dd></div>
            <div><dt>Browser API path</dt><dd><code>/api/backend</code></dd></div>
            <div><dt>Application API</dt><dd>FastAPI · local service</dd></div>
            <div><dt>API documentation</dt><dd><a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">Open interactive docs <Icon name="arrow" /></a></dd></div>
            <div><dt>Application version</dt><dd><code>0.1.0</code></dd></div>
            <div><dt>Connection state</dt><dd><span className={`inline-state ${health?.status === "ok" ? "state-on" : "state-off"}`}><i />{health?.status === "ok" ? "Connected" : "Unavailable"}</span></dd></div>
          </dl>
        </section>

        <section className="config-card enterprise-config-card">
          <p className="eyebrow">Configuration</p><h2>Service-managed settings</h2><p>Models and local runtime limits remain fixed in the service configuration. API keys are managed securely above.</p>
          <div className="code-block"><code>AMA_VISUAL_ANALYSIS_MODEL</code><code>AMA_EMBEDDING_MODEL</code><code>AMA_RERANK_MODEL</code><code>AMA_ANSWER_MODEL</code><code>AMA_EMBEDDING_CACHE_MAX_ENTRIES</code><code>AMA_SQLITE_BUSY_TIMEOUT_MS</code><code>AMA_VECTOR_STORE</code></div>
        </section>
      </div>
    </div>
  );
}
