import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantWorkspace } from "./assistant-workspace";

const document = {
  id: "doc-1",
  original_filename: "pump-manual.pdf",
  format: "pdf",
  size_bytes: 1200,
  title: "Pump manual",
  page_count: 12,
  chunk_count: 8,
  extractor_name: "pypdf",
  extractor_version: "1",
  created_at: "2026-07-13T09:00:00Z",
  lifecycle_status: "current" as const,
  revision: 1,
  supersedes_document_id: null,
  lifecycle_updated_at: "2026-07-13T09:00:00Z",
};

const health = {
  status: "ok",
  storage: "ok",
  ocr: "available" as const,
  ocr_engine: "tesseract",
  ocr_version: "5.5.0",
  visual_analysis: "available" as const,
  visual_analysis_model: "test-vision",
  embeddings: "enabled",
  embedding_model: "test-embedding",
  answers: "enabled",
  answer_model: "test-answer",
};

describe("AssistantWorkspace", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.includes("/health")) return Response.json(health);
      if (url.includes("/documents")) return Response.json({ items: [document], limit: 100, offset: 0 });
      if (url.includes("/answers") && options?.method === "POST") {
        return Response.json({
          question: "How do I isolate the pump?",
          answerable: true,
          answer: "Disconnect and lock out the electrical supply [S1].",
          citations: [{
            source_id: "S1",
            score: 0.94,
            document,
            chunk_id: "chunk-1",
            chunk_sequence: 2,
            parent_context_id: "parent-1",
            excerpt: "Disconnect and lock out the electrical supply.",
            page_start: 8,
            page_end: 8,
            headings: ["Isolation"],
            line_start: null,
            line_end: null,
          }],
          model: "test-answer",
          usage: { input_tokens: 20, output_tokens: 8 },
        });
      }
      return Response.json({}, { status: 404 });
    }));
  });

  it("asks a question and presents the verified evidence", async () => {
    render(<AssistantWorkspace />);

    expect(await screen.findByText("Knowledge base ready")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Maintenance question"), {
      target: { value: "How do I isolate the pump?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask assistant" }));

    expect(await screen.findByText("Based on your manuals")).toBeInTheDocument();
    expect(screen.getByText("[S1]")).toBeInTheDocument();
    expect(screen.getByText("Sources verified")).toBeInTheDocument();
    expect(screen.getAllByText("Pump manual")).toHaveLength(2);
    expect(screen.getByText("Disconnect and lock out the electrical supply.")).toBeInTheDocument();
    expect(screen.getByText("Page 8")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/backend/documents?limit=100&lifecycle_status=current",
      { cache: "no-store" },
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/backend/answers",
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("fills the question box from a worker-friendly suggestion", async () => {
    render(<AssistantWorkspace />);
    await screen.findByText("Knowledge base ready");

    fireEvent.click(screen.getByRole("button", { name: /abnormal vibration/i }));

    expect(screen.getByLabelText("Maintenance question")).toHaveValue(
      "What does the manual say about abnormal vibration?",
    );
  });

  it("explains where to go when answer providers are not configured", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/health")) {
        return Response.json({ ...health, embeddings: "disabled", answers: "disabled" });
      }
      return Response.json({ items: [document], limit: 100, offset: 0 });
    }));

    render(<AssistantWorkspace />);

    expect(await screen.findByText("The assistant needs to be connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view settings/i })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("button", { name: "Ask assistant" })).toBeDisabled();
  });
});
