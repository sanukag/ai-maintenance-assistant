"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { type Health, readJson } from "@/lib/api";

type StatusRow = { label: string; value: string; detail: string; available: boolean };

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

  const rows: StatusRow[] = [
    { label: "Document storage", value: health?.storage === "ok" ? "Online" : "Unavailable", detail: "SQLite and local document files", available: health?.storage === "ok" },
    { label: "OCR", value: health?.ocr === "available" ? "Available" : health?.ocr === "disabled" ? "Disabled" : "Unavailable", detail: health?.ocr_engine ? `${health.ocr_engine}${health.ocr_version ? ` ${health.ocr_version}` : ""}` : "No engine configured", available: health?.ocr === "available" },
    { label: "Visual analysis", value: health?.visual_analysis === "available" ? "Available" : health?.visual_analysis === "disabled" ? "Disabled" : "Unavailable", detail: health?.visual_analysis_model ?? "No model configured", available: health?.visual_analysis === "available" },
    { label: "Embeddings", value: health?.embeddings === "enabled" ? "Enabled" : "Disabled", detail: health?.embedding_model ?? "No model configured", available: health?.embeddings === "enabled" },
    { label: "Answer generation", value: health?.answers === "enabled" ? "Enabled" : "Disabled", detail: health?.answer_model ?? "No model configured", available: health?.answers === "enabled" },
  ];

  return (
    <div className="page page-settings">
      <header className="page-header">
        <div><p className="eyebrow">Administration</p><h1>Settings</h1><p>Runtime status, configuration reference and developer information.</p></div>
        <button className="secondary-button" type="button" onClick={refresh} disabled={loading}><Icon name="refresh" className={loading ? "spinning" : ""} /> Refresh status</button>
      </header>

      {error && <div className="toast-message toast-error" role="alert"><span>!</span>{error}</div>}

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
          <p className="eyebrow">Configuration</p><h2>Environment variables</h2><p>Provider settings are managed in the service environment. Restart the service after changing them.</p>
          <div className="code-block"><code>AMA_OCR_PROVIDER</code><code>AMA_VISUAL_ANALYSIS_PROVIDER</code><code>AMA_EMBEDDING_PROVIDER</code><code>AMA_ANSWER_PROVIDER</code><code>OPENAI_API_KEY</code></div>
        </section>
      </div>
    </div>
  );
}
