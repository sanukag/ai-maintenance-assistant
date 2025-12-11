"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { type DocumentList, type DocumentRecord, formatFileSize, readJson } from "@/lib/api";

type Message = { tone: "success" | "error"; text: string };
type DestructiveAction = "archive" | "delete";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}

function statusLabel(status: DocumentRecord["lifecycle_status"]): string {
  if (status === "current") return "Current";
  if (status === "superseded") return "Superseded";
  return "Archived";
}

export function ManualLibrary() {
  const input = useRef<HTMLInputElement>(null);
  const replacementInput = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [replacementFile, setReplacementFile] = useState<File | null>(null);
  const [selected, setSelected] = useState<DocumentRecord | null>(null);
  const [history, setHistory] = useState<DocumentRecord[]>([]);
  const [pendingAction, setPendingAction] = useState<DestructiveAction | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);

  const currentDocuments = useMemo(
    () => documents.filter((document) => document.lifecycle_status === "current"),
    [documents],
  );
  const visibleDocuments = showAll ? documents : currentDocuments;

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
      .catch((error: Error) => { if (active) setMessage({ tone: "error", text: error.message }); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function chooseFile(selectedFile: File | undefined, replace = false) {
    if (!selectedFile) return;
    const extension = selectedFile.name.split(".").pop()?.toLowerCase();
    if (!extension || !["pdf", "txt", "md", "png", "jpg", "jpeg"].includes(extension)) {
      setMessage({ tone: "error", text: "Choose a PDF, image, text or Markdown file." });
      return;
    }
    if (replace) setReplacementFile(selectedFile);
    else setFile(selectedFile);
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

  async function openManagement(document: DocumentRecord) {
    setSelected(document);
    setHistory([]);
    setPendingAction(null);
    setReplacementFile(null);
    try {
      const response = await fetch(`/api/backend/documents/${document.id}/revisions`, { cache: "no-store" });
      setHistory((await readJson<{ items: DocumentRecord[] }>(response)).items);
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Revision history could not be loaded." });
    }
  }

  function closeManagement() {
    if (working) return;
    setSelected(null);
    setHistory([]);
    setPendingAction(null);
    setReplacementFile(null);
  }

  async function replaceManual() {
    if (!selected || !replacementFile || working) return;
    setWorking(true);
    const body = new FormData();
    body.append("file", replacementFile);
    try {
      const response = await fetch(`/api/backend/documents/${selected.id}/revisions`, { method: "POST", body });
      const result = await readJson<{ document: DocumentRecord }>(response);
      setMessage({ tone: "success", text: `${result.document.title} revision ${result.document.revision} is now current.` });
      closeManagementAfterWork();
      await loadDocuments();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "The replacement could not be installed." });
    } finally {
      setWorking(false);
    }
  }

  async function reindexManual() {
    if (!selected || working) return;
    setWorking(true);
    try {
      const response = await fetch(`/api/backend/documents/${selected.id}/reindex`, { method: "POST" });
      const result = await readJson<{ embeddings: { chunk_count: number } }>(response);
      setMessage({ tone: "success", text: `${result.embeddings.chunk_count} sections were re-indexed successfully.` });
      closeManagementAfterWork();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "The manual could not be re-indexed." });
    } finally {
      setWorking(false);
    }
  }

  async function confirmDestructiveAction() {
    if (!selected || !pendingAction || working) return;
    setWorking(true);
    try {
      const endpoint = pendingAction === "archive" ? `${selected.id}/archive` : selected.id;
      const response = await fetch(`/api/backend/documents/${endpoint}`, {
        method: pendingAction === "archive" ? "POST" : "DELETE",
      });
      if (!response.ok) await readJson(response);
      setMessage({
        tone: "success",
        text: pendingAction === "archive" ? `${selected.title} was archived and will no longer be used for answers.` : `${selected.title} was permanently deleted.`,
      });
      closeManagementAfterWork();
      await loadDocuments();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "The lifecycle change could not be completed." });
    } finally {
      setWorking(false);
    }
  }

  function closeManagementAfterWork() {
    setSelected(null);
    setHistory([]);
    setPendingAction(null);
    setReplacementFile(null);
  }

  return (
    <div className="page page-manuals">
      <header className="page-header">
        <div><p className="eyebrow">Knowledge base</p><h1>Manuals</h1><p>Keep the assistant grounded in current, approved maintenance information.</p></div>
        <button className="primary-button" type="button" onClick={() => input.current?.click()}><Icon name="upload" /> Add manual</button>
      </header>

      {message && <div className={`toast-message toast-${message.tone}`} role="status"><span>{message.tone === "success" ? <Icon name="check" /> : "!"}</span>{message.text}<button type="button" aria-label="Dismiss message" onClick={() => setMessage(null)}><Icon name="close" /></button></div>}

      <section className="manual-overview-grid">
        <div className={`upload-panel ${dragging ? "upload-panel-dragging" : ""}`} onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
          <input ref={input} type="file" accept=".pdf,.txt,.md,.png,.jpg,.jpeg" onChange={onFileChange} hidden />
          <span className="upload-illustration"><Icon name="upload" /></span>
          <div><p className="eyebrow">Add knowledge</p><h2>Drop a manual here</h2><p>or choose a PDF, image, text or Markdown file from your computer</p></div>
          {file ? (
            <div className="selected-file"><span className="file-type">{file.name.split(".").pop()?.toUpperCase()}</span><span><strong>{file.name}</strong><small>{formatFileSize(file.size)}</small></span><button type="button" aria-label="Remove selected file" onClick={() => setFile(null)}><Icon name="close" /></button></div>
          ) : <button className="secondary-button" type="button" onClick={() => input.current?.click()}>Choose a file</button>}
          {file && <button className="primary-button upload-submit" type="button" onClick={upload} disabled={uploading}>{uploading ? <span className="button-spinner" /> : <Icon name="upload" />}{uploading ? "Reading manual" : "Add to library"}</button>}
          <p className="upload-limit">Maximum file size: 25 MB</p>
        </div>

        <div className="library-summary">
          <div className="summary-decoration"><span /><span /><span /></div>
          <p className="eyebrow">Library health</p>
          <strong>{loading ? "—" : currentDocuments.length}</strong>
          <h2>{currentDocuments.length === 1 ? "current manual" : "current manuals"}</h2>
          <p>{currentDocuments.reduce((total, document) => total + document.chunk_count, 0)} approved sections are available to the assistant.</p>
          <div className="supported-row"><span><Icon name="check" /> Current only</span><span><Icon name="history" /> Revisions retained</span></div>
        </div>
      </section>

      <section className="library-section" aria-labelledby="library-heading">
        <div className="section-heading-row library-heading-row">
          <div><p className="eyebrow">Your documents</p><h2 id="library-heading">Manual library</h2></div>
          <div className="revision-filter" aria-label="Manual view"><button type="button" className={!showAll ? "active" : ""} onClick={() => setShowAll(false)}>Current</button><button type="button" className={showAll ? "active" : ""} onClick={() => setShowAll(true)}>All revisions</button></div>
        </div>
        {loading ? <div className="manual-loading"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div> : visibleDocuments.length === 0 ? (
          <div className="empty-library"><span><Icon name="manual" /></span><h3>{showAll ? "No manuals yet" : "No current manuals"}</h3><p>{showAll ? "Add your first approved document to start asking grounded questions." : "Add or replace a manual to restore the active knowledge base."}</p><button className="secondary-button" type="button" onClick={() => input.current?.click()}>Choose a manual</button></div>
        ) : (
          <div className="manual-table-wrap">
            <table className="manual-table">
              <thead><tr><th>Manual</th><th>Revision</th><th>Coverage</th><th>Added</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{visibleDocuments.map((document) => (
                <tr key={document.id} className={`manual-row-${document.lifecycle_status}`}>
                  <td><span className={`document-icon document-${document.format}`}><Icon name="file" /></span><span><strong>{document.title}</strong><small>{document.original_filename} · {formatFileSize(document.size_bytes)}</small></span></td>
                  <td><span className="revision-chip">Rev {document.revision}</span><small>{document.format === "markdown" ? "MD" : document.format.toUpperCase()}</small></td>
                  <td><strong>{document.chunk_count}</strong> sections{document.page_count ? <small>{document.page_count} pages</small> : null}</td>
                  <td>{formatDate(document.created_at)}</td>
                  <td><span className={`lifecycle-status status-${document.lifecycle_status}`}><i /> {statusLabel(document.lifecycle_status)}</span></td>
                  <td><button className="manage-manual-button" type="button" onClick={() => openManagement(document)}>Manage <Icon name="chevron" /></button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeManagement(); }}>
          <section className="manual-dialog" role="dialog" aria-modal="true" aria-labelledby="manual-dialog-title">
            <header><div><p className="eyebrow">Manual controls</p><h2 id="manual-dialog-title">{selected.title}</h2><p>{selected.original_filename} · Revision {selected.revision}</p></div><button type="button" aria-label="Close manual controls" onClick={closeManagement} disabled={working}><Icon name="close" /></button></header>

            <div className="dialog-status-line"><span className={`lifecycle-status status-${selected.lifecycle_status}`}><i /> {statusLabel(selected.lifecycle_status)}</span><span>{selected.chunk_count} indexed sections</span><span>Added {formatDate(selected.created_at)}</span></div>

            <div className="revision-history"><div className="dialog-section-heading"><h3>Revision history</h3><span>{history.length || "…"} retained</span></div><ol>{history.map((revision) => <li key={revision.id}><span className={`history-dot status-${revision.lifecycle_status}`} /><div><strong>Revision {revision.revision}</strong><small>{revision.original_filename} · {formatDate(revision.created_at)}</small></div><span>{statusLabel(revision.lifecycle_status)}</span></li>)}</ol></div>

            {selected.lifecycle_status === "current" && !pendingAction && (
              <div className="manual-actions-grid">
                <article><span><Icon name="refresh" /></span><div><h3>Install a newer revision</h3><p>The current copy is retained as superseded and excluded from answers.</p><input ref={replacementInput} type="file" accept=".pdf,.txt,.md,.png,.jpg,.jpeg" hidden onChange={(event) => chooseFile(event.target.files?.[0], true)} />{replacementFile ? <div className="replacement-choice"><strong>{replacementFile.name}</strong><button type="button" onClick={() => setReplacementFile(null)}><Icon name="close" /></button></div> : <button type="button" className="text-action" onClick={() => replacementInput.current?.click()}>Choose replacement</button>}{replacementFile && <button type="button" className="primary-button compact-button" onClick={replaceManual} disabled={working}>{working ? "Installing revision" : `Install as revision ${selected.revision + 1}`}</button>}</div></article>
                <article><span><Icon name="spark" /></span><div><h3>Refresh search index</h3><p>Regenerate vectors using the embedding model currently configured.</p><button type="button" className="text-action" onClick={reindexManual} disabled={working}>Re-index manual</button></div></article>
              </div>
            )}

            {!pendingAction ? (
              <footer><div><button type="button" className="archive-action" onClick={() => setPendingAction("archive")} disabled={selected.lifecycle_status === "archived" || working}><Icon name="archive" /> Archive</button><button type="button" className="delete-action" onClick={() => setPendingAction("delete")} disabled={working}><Icon name="trash" /> Delete permanently</button></div><button type="button" className="secondary-button" onClick={closeManagement} disabled={working}>Done</button></footer>
            ) : (
              <div className={`confirmation-panel confirmation-${pendingAction}`} role="alert"><span><Icon name={pendingAction === "delete" ? "trash" : "archive"} /></span><div><h3>{pendingAction === "delete" ? "Permanently delete this manual?" : "Archive this manual?"}</h3><p>{pendingAction === "delete" ? "The original file, metadata, indexed sections and vectors will be removed. This cannot be undone." : "It will remain in revision history but will no longer contribute to maintenance answers."}</p></div><div><button type="button" className="secondary-button" onClick={() => setPendingAction(null)} disabled={working}>Cancel</button><button type="button" className={pendingAction === "delete" ? "danger-button" : "primary-button"} onClick={confirmDestructiveAction} disabled={working}>{working ? "Working…" : pendingAction === "delete" ? "Delete permanently" : "Archive manual"}</button></div></div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
