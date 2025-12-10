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
};

describe("ManualLibrary", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
      if (options?.method === "POST") {
        return Response.json({ status: "completed", document }, { status: 201 });
      }
      return Response.json({ items: [document], limit: 100, offset: 0 });
    }));
  });

  it("shows indexed manuals in worker-friendly language", async () => {
    render(<ManualLibrary />);

    expect(await screen.findByText("Pump manual")).toBeInTheDocument();
    expect(screen.getByText("7 indexed sections are available to the knowledge base.")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
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
});
