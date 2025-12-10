"use client";

import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { type DocumentList, type DocumentRecord, formatFileSize, readJson } from "@/lib/api";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}

export function ManualLibrary() {
  const input = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  async function loadDocuments() {
    try {
      const response = await fetch("/api/backend/documents?limit=100", { cache: "no-store" });
      setDocuments((await readJson<DocumentList>(response)).items);
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Manuals could not be loaded." });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    fetch("/api/backend/documents?limit=100", { cache: "no-store" })
      .then(readJson<DocumentList>)
      .then((result) => { if (active) setDocuments(result.items); })
      .catch((error: Error) => {
        if (active) setMessage({ tone: "error", text: error.message });
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function chooseFile(selected: File | undefined) {
    if (!selected) return;
    const extension = selected.name.split(".").pop()?.toLowerCase();
    if (!extension || !["pdf", "txt", "md"].includes(extension)) {
      setMessage({ tone: "error", text: "Choose a PDF, text or Markdown file." });
      return;
    }
    setFile(selected);
    setMessage(null);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    chooseFile(event.target.files?.[0]);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  }

  async function upload() {
    if (!file || uploading) return;
    setUploading(true);
    setMessage(null);
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch("/api/backend/documents", { method: "POST", body });
      const result = await readJson<{ status: string; document: DocumentRecord }>(response);
      setMessage({
        tone: "success",
        text: result.status === "already_exists" ? `${result.document.title} is already in the library.` : `${result.document.title} is ready to use.`,
      });
      setFile(null);
      if (input.current) input.current.value = "";
      await loadDocuments();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "The manual could not be added." });
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="page page-manuals">
      <header className="page-header">
        <div><p className="eyebrow">Knowledge base</p><h1>Manuals</h1><p>Keep the assistant grounded in current, approved maintenance information.</p></div>
        <button className="primary-button" type="button" onClick={() => input.current?.click()}><Icon name="upload" /> Add manual</button>
      </header>

      {message && <div className={`toast-message toast-${message.tone}`} role="status"><span>{message.tone === "success" ? <Icon name="check" /> : "!"}</span>{message.text}<button type="button" aria-label="Dismiss message" onClick={() => setMessage(null)}><Icon name="close" /></button></div>}

      <section className="manual-overview-grid">
        <div
          className={`upload-panel ${dragging ? "upload-panel-dragging" : ""}`}
          onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input ref={input} type="file" accept=".pdf,.txt,.md" onChange={onFileChange} hidden />
          <span className="upload-illustration"><Icon name="upload" /></span>
          <div><p className="eyebrow">Add knowledge</p><h2>Drop a manual here</h2><p>or choose a PDF, text or Markdown file from your computer</p></div>
          {file ? (
            <div className="selected-file">
              <span className="file-type">{file.name.split(".").pop()?.toUpperCase()}</span>
              <span><strong>{file.name}</strong><small>{formatFileSize(file.size)}</small></span>
              <button type="button" aria-label="Remove selected file" onClick={() => setFile(null)}><Icon name="close" /></button>
            </div>
          ) : <button className="secondary-button" type="button" onClick={() => input.current?.click()}>Choose a file</button>}
          {file && <button className="primary-button upload-submit" type="button" onClick={upload} disabled={uploading}>{uploading ? <span className="button-spinner" /> : <Icon name="upload" />}{uploading ? "Reading manual" : "Add to library"}</button>}
          <p className="upload-limit">Maximum file size: 25 MB</p>
        </div>

        <div className="library-summary">
          <div className="summary-decoration"><span /><span /><span /></div>
          <p className="eyebrow">Library health</p>
          <strong>{loading ? "—" : documents.length}</strong>
          <h2>{documents.length === 1 ? "manual indexed" : "manuals indexed"}</h2>
          <p>{documents.reduce((total, document) => total + document.chunk_count, 0)} indexed sections are available to the knowledge base.</p>
          <div className="supported-row"><span><Icon name="check" /> PDF</span><span><Icon name="check" /> Text</span><span><Icon name="check" /> Markdown</span></div>
        </div>
      </section>

      <section className="library-section" aria-labelledby="library-heading">
        <div className="section-heading-row"><div><p className="eyebrow">Your documents</p><h2 id="library-heading">Manual library</h2></div><span>{documents.length} total</span></div>
        {loading ? <div className="manual-loading"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div> : documents.length === 0 ? (
          <div className="empty-library"><span><Icon name="manual" /></span><h3>No manuals yet</h3><p>Add your first approved document to start asking grounded questions.</p><button className="secondary-button" type="button" onClick={() => input.current?.click()}>Choose a manual</button></div>
        ) : (
          <div className="manual-table-wrap">
            <table className="manual-table">
              <thead><tr><th>Manual</th><th>Type</th><th>Coverage</th><th>Added</th><th>Status</th></tr></thead>
              <tbody>{documents.map((document) => (
                <tr key={document.id}>
                  <td><span className={`document-icon document-${document.format}`}><Icon name="file" /></span><span><strong>{document.title}</strong><small>{document.original_filename} · {formatFileSize(document.size_bytes)}</small></span></td>
                  <td><span className="format-chip">{document.format === "markdown" ? "MD" : document.format.toUpperCase()}</span></td>
                  <td><strong>{document.chunk_count}</strong> sections{document.page_count ? <small>{document.page_count} pages</small> : null}</td>
                  <td>{formatDate(document.created_at)}</td>
                  <td><span className="indexed-status"><i /> Ready</span></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
