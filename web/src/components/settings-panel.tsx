"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { type Health, readJson } from "@/lib/api";

export function SettingsPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/backend/health", { cache: "no-store" });
      setHealth(await readJson<Health>(response));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Service status could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    fetch("/api/backend/health", { cache: "no-store" })
      .then(readJson<Health>)
      .then((result) => { if (active) setHealth(result); })
      .catch((requestError: Error) => { if (active) setError(requestError.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const online = health?.status === "ok";

  return (
    <div className="page page-settings">
      <header className="page-header">
        <div><p className="eyebrow">Workspace configuration</p><h1>Settings</h1><p>System status, provider information and developer reference.</p></div>
        <button className="secondary-button" type="button" onClick={refresh} disabled={loading}><Icon name="refresh" className={loading ? "spinning" : ""} /> Refresh status</button>
      </header>

      {error && <div className="toast-message toast-error" role="alert"><span>!</span>{error}</div>}

      <section className="settings-hero">
        <div className="settings-hero-copy"><span className={`large-status ${online ? "large-status-online" : ""}`}><i />{loading ? "Checking service" : online ? "All local services operational" : "Service needs attention"}</span><h2>Your maintenance workspace</h2><p>The interface talks to the local API through a private server-side connection. Provider credentials are never displayed here.</p></div>
        <div className="settings-orbit"><span><Icon name="server" /></span><i className="orbit-one" /><i className="orbit-two" /><b>LOCAL</b></div>
      </section>

      <div className="settings-layout">
        <section className="settings-main">
          <div className="settings-section-heading"><div><p className="eyebrow">Service status</p><h2>Connected capabilities</h2></div><span>Live from the API</span></div>
          <div className="capability-grid">
            <article className="capability-card"><span className="capability-icon green"><Icon name="database" /></span><div><p>Local storage</p><h3>{health?.storage === "ok" ? "Ready" : "Unavailable"}</h3><small>Documents, source records and vectors</small></div><span className={`capability-state ${health?.storage === "ok" ? "state-on" : "state-off"}`}><i />{health?.storage === "ok" ? "Online" : "Offline"}</span></article>
            <article className="capability-card"><span className="capability-icon green"><Icon name="file" /></span><div><p>Scanned document OCR</p><h3>{health?.ocr === "available" ? "Available" : health?.ocr === "disabled" ? "Disabled" : "Unavailable"}</h3><small>{health?.ocr_engine ? `${health.ocr_engine}${health.ocr_version ? ` ${health.ocr_version}` : ""}` : "No OCR engine configured"}</small></div><span className={`capability-state ${health?.ocr === "available" ? "state-on" : "state-off"}`}><i />{health?.ocr === "available" ? "Local" : "Setup"}</span></article>
            <article className="capability-card"><span className="capability-icon blue"><Icon name="image" /></span><div><p>Images and diagrams</p><h3>{health?.visual_analysis === "available" ? "Understood" : health?.visual_analysis === "disabled" ? "Disabled" : "Unavailable"}</h3><small>{health?.visual_analysis_model ?? "No vision model configured"}</small></div><span className={`capability-state ${health?.visual_analysis === "available" ? "state-on" : "state-off"}`}><i />{health?.visual_analysis === "available" ? "Active" : "Setup"}</span></article>
            <article className="capability-card"><span className="capability-icon orange"><Icon name="spark" /></span><div><p>Semantic search</p><h3>{health?.embeddings === "enabled" ? "Enabled" : "Disabled"}</h3><small>{health?.embedding_model ?? "No embedding model configured"}</small></div><span className={`capability-state ${health?.embeddings === "enabled" ? "state-on" : "state-off"}`}><i />{health?.embeddings === "enabled" ? "Active" : "Setup"}</span></article>
            <article className="capability-card"><span className="capability-icon blue"><Icon name="shield" /></span><div><p>Grounded answers</p><h3>{health?.answers === "enabled" ? "Enabled" : "Disabled"}</h3><small>{health?.answer_model ?? "No answer model configured"}</small></div><span className={`capability-state ${health?.answers === "enabled" ? "state-on" : "state-off"}`}><i />{health?.answers === "enabled" ? "Active" : "Setup"}</span></article>
          </div>

          <div className="developer-card">
            <div className="developer-heading"><span><Icon name="server" /></span><div><p className="eyebrow">Developer information</p><h2>Runtime reference</h2></div></div>
            <dl className="runtime-list">
              <div><dt>Web application</dt><dd>Next.js 16 · App Router</dd></div>
              <div><dt>Browser API path</dt><dd><code>/api/backend</code></dd></div>
              <div><dt>Application API</dt><dd>FastAPI · local service</dd></div>
              <div><dt>API documentation</dt><dd><a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">Open interactive docs <Icon name="arrow" /></a></dd></div>
              <div><dt>Application version</dt><dd><code>0.1.0</code></dd></div>
              <div><dt>Connection state</dt><dd><span className={`inline-state ${online ? "state-on" : "state-off"}`}><i />{online ? "Connected" : "Unavailable"}</span></dd></div>
            </dl>
          </div>
        </section>

        <aside className="settings-side">
          <article className="privacy-card"><span className="privacy-icon"><Icon name="shield" /></span><p className="eyebrow">Privacy boundary</p><h2>What stays local?</h2><ul><li><Icon name="check" /> Original manual files</li><li><Icon name="check" /> SQLite document records</li><li><Icon name="check" /> Stored embedding vectors</li></ul><p className="privacy-note">When providers are enabled, rendered pages for visual analysis, selected chunk text and questions are sent to OpenAI. API keys remain in the server environment.</p></article>
          <article className="config-card"><p className="eyebrow">Configuration</p><h2>Environment variables</h2><p>Provider settings are controlled outside this interface so credentials cannot be changed accidentally.</p><div className="code-block"><code>AMA_OCR_PROVIDER</code><code>AMA_VISUAL_ANALYSIS_PROVIDER</code><code>AMA_EMBEDDING_PROVIDER</code><code>AMA_ANSWER_PROVIDER</code><code>OPENAI_API_KEY</code></div><p>Restart the service after changing runtime configuration.</p></article>
        </aside>
      </div>
    </div>
  );
}
