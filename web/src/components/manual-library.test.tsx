import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ManualLibrary } from "./manual-library";

const document = {
  id: "doc-1",
  original_filename: "pump-manual.pdf",
  format: "pdf",
  size_bytes: 2048,
  title: "Pump manual",
  page_count: 10,
  chunk_count: 7,
  extractor_name: "pypdf",
  extractor_version: "1",
  created_at: "2026-07-13T09:00:00Z",
  lifecycle_status: "current" as const,
  revision: 1,
  supersedes_document_id: null,
  lifecycle_updated_at: "2026-07-13T09:00:00Z",
};

const replacement = {
  ...document,
  id: "doc-2",
  original_filename: "pump-manual-v2.pdf",
  revision: 2,
  supersedes_document_id: document.id,
};

describe("ManualLibrary", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/revisions") && options?.method === "POST") {
        return Response.json({ status: "completed", document: replacement }, { status: 201 });
      }
      if (url.endsWith("/revisions")) {
        return Response.json({ items: [document] });
      }
      if (url.endsWith("/archive") && options?.method === "POST") {
        return Response.json({ ...document, lifecycle_status: "archived" });
      }
      if (options?.method === "DELETE") return new Response(null, { status: 204 });
      if (options?.method === "POST") {
        return Response.json({ status: "completed", document }, { status: 201 });
      }
      return Response.json({ items: [document], limit: 100, offset: 0 });
    }));
  });

  it("shows indexed manuals in worker-friendly language", async () => {
    render(<ManualLibrary />);

    expect(await screen.findByText("Pump manual")).toBeInTheDocument();
    expect(screen.getByText("7 approved sections are available to the assistant.")).toBeInTheDocument();
    expect(screen.getAllByText("Current")).toHaveLength(2);
  });

  it("installs a replacement as the next retained revision", async () => {
    const { container } = render(<ManualLibrary />);
    await screen.findByText("Pump manual");
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(await screen.findByText("Revision history")).toBeInTheDocument();

    const inputs = container.querySelectorAll('input[type="file"]');
    const file = new File(["Updated procedure"], "pump-manual-v2.pdf", { type: "application/pdf" });
    fireEvent.change(inputs[1], { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Install as revision 2" }));

    expect(await screen.findByText("Pump manual revision 2 is now current.")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/backend/documents/doc-1/revisions",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    ));
  });

  it("requires confirmation before archiving a current manual", async () => {
    render(<ManualLibrary />);
    await screen.findByText("Pump manual");
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

    expect(screen.getByText("Archive this manual?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Archive manual" }));

    expect(await screen.findByText(/was archived and will no longer be used for answers/i)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/backend/documents/doc-1/archive",
      { method: "POST" },
    );
  });

  it("requires explicit confirmation before permanent deletion", async () => {
    render(<ManualLibrary />);
    await screen.findByText("Pump manual");
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete permanently" }));

    expect(screen.getByText("Permanently delete this manual?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));

    expect(await screen.findByText(/was permanently deleted/i)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/backend/documents/doc-1",
      { method: "DELETE" },
    );
  });

  it("uploads a supported manual and confirms it is ready", async () => {
    const { container } = render(<ManualLibrary />);
    await screen.findByText("Pump manual");
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["Pump isolation procedure"], "new-manual.txt", { type: "text/plain" });

    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

    expect(await screen.findByText("Pump manual is ready to use.")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/backend/documents",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    ));
  });

  it("accepts a scanned image manual", async () => {
    const { container } = render(<ManualLibrary />);
    await screen.findByText("Pump manual");
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["scan"], "panel-procedure.png", { type: "image/png" });

    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText("panel-procedure.png")).toBeInTheDocument();
    expect(screen.getByText("PNG")).toBeInTheDocument();
  });
});
