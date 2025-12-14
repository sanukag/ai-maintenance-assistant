import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiagnosticWorkspace } from "./diagnostic-workspace";

const metadata = { brand: ["Acme"], machine: ["P-100"], site: ["North plant"], document_type: ["Service manual"] };
const document = {
  id: "doc-1", original_filename: "pump.pdf", format: "pdf", size_bytes: 1000,
  title: "Pump manual", page_count: 12, chunk_count: 6, extractor_name: "pypdf",
  extractor_version: "1", created_at: "2026-07-14T08:00:00Z", lifecycle_status: "current",
  revision: 1, supersedes_document_id: null, lifecycle_updated_at: "2026-07-14T08:00:00Z", metadata,
};
const health = {
  status: "ok", storage: "ok", ocr: "available", ocr_engine: "tesseract", ocr_version: "5",
  visual_analysis: "available", visual_analysis_model: "vision", embeddings: "enabled",
  embedding_model: "embedding", answers: "enabled", answer_model: "answer",
  diagnostics: "enabled", diagnostic_model: "diagnostic", vector_store: "qdrant",
  vector_index: "available", reranking: "enabled", rerank_model: "reranker",
};
const citation = {
  source_id: "S1", score: 0.92, document_id: "doc-1", document_title: "Pump manual",
  original_filename: "pump.pdf", chunk_id: "chunk-1", chunk_sequence: 2,
  parent_context_id: "parent-1", excerpt: "Inspect the visible overload indicator.",
  page_start: 8, page_end: 8, headings: ["Troubleshooting"], line_start: null, line_end: null,
};
const detail = {
  session: {
    id: "diagnostic-1", title: "Pump trips after five minutes", status: "active",
    safety_status: "non_intrusive_only", document_id: null, metadata,
    state: {
      symptoms: ["Pump trips after five minutes"], observations: ["Trips only when loaded"],
      measurements: [], completed_checks: [], summary: "The pump trips under load.",
      hypotheses: [{ title: "Motor overload", likelihood: "medium", rationale: "The trip occurs under load.", supporting_source_ids: ["S1"], contrary_observations: [] }],
    },
    created_at: "2026-07-14T09:00:00Z", updated_at: "2026-07-14T09:01:00Z", turn_count: 2,
  },
  turns: [
    { id: "turn-1", sequence: 0, role: "user", content: "Pump trips after five minutes", action: null, payload: {}, created_at: "2026-07-14T09:00:00Z" },
    { id: "turn-2", sequence: 1, role: "assistant", content: "Inspect the visible overload indicator [S1].", action: "suggest_check", payload: { citations: [citation] }, created_at: "2026-07-14T09:01:00Z" },
  ],
};

describe("DiagnosticWorkspace", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/diagnostics");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.includes("/health")) return Response.json(health);
      if (url.includes("/metadata/options")) return Response.json(metadata);
      if (url.includes("/documents")) return Response.json({ items: [document], limit: 100, offset: 0 });
      if (url.includes("/diagnostic-sessions") && options?.method === "POST") return Response.json(detail, { status: url.endsWith("/diagnostic-sessions") ? 201 : 200 });
      if (url.includes("/diagnostic-sessions?")) return Response.json({ items: [], limit: 50, offset: 0 });
      if (url.includes("/diagnostic-sessions/")) return Response.json(detail);
      return Response.json({}, { status: 404 });
    }));
  });

  it("starts a guided investigation and displays live hypotheses and citations", async () => {
    render(<DiagnosticWorkspace />);

    expect(await screen.findByText("Describe what the machine is doing")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Fault description"), { target: { value: "Pump trips after five minutes" } });
    fireEvent.change(screen.getByLabelText("Equipment safety state"), { target: { value: "non_intrusive_only" } });
    fireEvent.click(screen.getByRole("button", { name: "Start diagnosis" }));

    expect(await screen.findByText("Recommended check")).toBeInTheDocument();
    expect(screen.getByText("Motor overload")).toBeInTheDocument();
    expect(screen.getByText("The pump trips under load.")).toBeInTheDocument();
    expect(screen.getByText("Page 8")).toBeInTheDocument();
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([url, options]) => String(url).endsWith("/diagnostic-sessions") && options?.method === "POST");
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ message: "Pump trips after five minutes", safety_status: "non_intrusive_only" });
    });
  });

  it("reopens and continues an earlier investigation", async () => {
    window.history.replaceState({}, "", "/diagnostics?session=diagnostic-1");
    render(<DiagnosticWorkspace />);

    expect(await screen.findByText("Recommended check")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Observation, reading or follow-up question"), { target: { value: "Why does that matter?" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue diagnosis" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/backend/diagnostic-sessions/diagnostic-1/turns",
      expect.objectContaining({ method: "POST" }),
    ));
  });
});
