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
  metadata: { brand: ["Acme", "Acme Industrial"], machine: ["P-100"], site: ["North plant"], document_type: ["Service manual"] },
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

const citation = {
  source_id: "S1",
  score: 0.94,
  document_id: document.id,
  document_title: document.title,
  original_filename: document.original_filename,
  chunk_id: "chunk-1",
  chunk_sequence: 2,
  parent_context_id: "parent-1",
  excerpt: "Disconnect and lock out the electrical supply.",
  page_start: 8,
  page_end: 8,
  headings: ["Isolation"],
  line_start: null,
  line_end: null,
};

const conversation = {
  id: "conversation-1",
  title: "How do I isolate the pump?",
  created_at: "2026-07-14T09:00:00Z",
  updated_at: "2026-07-14T09:01:00Z",
  message_count: 2,
};

const conversationDetail = {
  conversation,
  messages: [
    { id: "message-1", sequence: 0, role: "user", content: "How do I isolate the pump?", created_at: conversation.created_at, scope_document_id: null, answerable: null, model: null, usage: null, citations: [], feedback: null, scope_metadata: document.metadata },
    { id: "message-2", sequence: 1, role: "assistant", content: "Disconnect and lock out the electrical supply [S1].", created_at: conversation.updated_at, scope_document_id: null, answerable: true, model: "test-answer", usage: { input_tokens: 20, output_tokens: 8 }, citations: [citation], feedback: null, scope_metadata: document.metadata },
  ],
};

describe("AssistantWorkspace", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.includes("/health")) return Response.json(health);
      if (url.includes("/metadata/options")) return Response.json(document.metadata);
      if (url.includes("/documents")) return Response.json({ items: [document], limit: 100, offset: 0 });
      if (url.includes("/answers") && options?.method === "POST") {
        return Response.json({
          conversation_id: conversation.id,
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
      if (url.includes(`/conversations/${conversation.id}`)) return Response.json(conversationDetail);
      if (url.includes("/conversations")) return Response.json({ items: [], limit: 50, offset: 0 });
      return Response.json({}, { status: 404 });
    }));
  });

  it("asks a question and presents the verified evidence", async () => {
    render(<AssistantWorkspace />);

    expect(await screen.findByText("Knowledge base ready")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter by brand"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText("Maintenance question"), {
      target: { value: "How do I isolate the pump?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask assistant" }));

    expect(await screen.findByText("Based on your manuals")).toBeInTheDocument();
    expect(screen.getByText("[S1]")).toBeInTheDocument();
    expect(screen.getByText("1 citation")).toBeInTheDocument();
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
    const answerCall = vi.mocked(fetch).mock.calls.find(([url, options]) =>
      String(url).includes("/answers") && options?.method === "POST",
    );
    expect(JSON.parse(String(answerCall?.[1]?.body))).toMatchObject({ brand: ["Acme"] });
  });

  it("fills the question box from a worker-friendly suggestion", async () => {
    render(<AssistantWorkspace />);
    await screen.findByText("Knowledge base ready");

    fireEvent.click(screen.getByRole("button", { name: /abnormal vibration/i }));

    expect(screen.getByLabelText("Maintenance question")).toHaveValue(
      "What does the manual say about abnormal vibration?",
    );
  });

  it("reopens a saved conversation and sends follow-ups to the same thread", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.includes("/health")) return Response.json(health);
      if (url.includes("/metadata/options")) return Response.json(document.metadata);
      if (url.includes("/documents")) return Response.json({ items: [document], limit: 100, offset: 0 });
      if (url.includes(`/conversations/${conversation.id}`)) return Response.json(conversationDetail);
      if (url.includes("/conversations")) return Response.json({ items: [conversation], limit: 50, offset: 0 });
      if (url.includes("/answers") && options?.method === "POST") return Response.json({ conversation_id: conversation.id });
      return Response.json({}, { status: 404 });
    }));

    render(<AssistantWorkspace />);
    window.dispatchEvent(new CustomEvent("assistant-conversation-selected", { detail: { conversationId: conversation.id } }));

    expect(await screen.findByText("Based on your manuals")).toBeInTheDocument();
    expect(screen.getAllByText("How do I isolate the pump?").length).toBeGreaterThan(1);
    fireEvent.change(screen.getByLabelText("Maintenance question"), {
      target: { value: "What should I inspect afterwards?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    await waitFor(() => {
      const answerCall = vi.mocked(fetch).mock.calls.find(([url, options]) =>
        String(url).includes("/answers") && options?.method === "POST",
      );
      expect(JSON.parse(String(answerCall?.[1]?.body))).toMatchObject({
        question: "What should I inspect afterwards?",
        conversation_id: conversation.id,
      });
    });
  });

  it("records and clears feedback for an assistant response", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      if (url.includes("/health")) return Response.json(health);
      if (url.includes("/metadata/options")) return Response.json(document.metadata);
      if (url.includes("/documents")) return Response.json({ items: [document], limit: 100, offset: 0 });
      if (url.endsWith("/feedback") && options?.method === "PUT") return Response.json({ rating: "up" });
      if (url.endsWith("/feedback") && options?.method === "DELETE") return new Response(null, { status: 204 });
      if (url.includes(`/conversations/${conversation.id}`)) return Response.json(conversationDetail);
      return Response.json({}, { status: 404 });
    }));
    render(<AssistantWorkspace />);
    window.dispatchEvent(new CustomEvent("assistant-conversation-selected", { detail: { conversationId: conversation.id } }));

    const helpful = await screen.findByRole("button", { name: "Mark response as helpful" });
    fireEvent.click(helpful);
    await waitFor(() => expect(helpful).toHaveAttribute("aria-pressed", "true"));
    expect(fetch).toHaveBeenCalledWith(
      `/api/backend/conversations/${conversation.id}/messages/message-2/feedback`,
      expect.objectContaining({ method: "PUT" }),
    );

    fireEvent.click(helpful);
    await waitFor(() => expect(helpful).toHaveAttribute("aria-pressed", "false"));
    expect(fetch).toHaveBeenCalledWith(
      `/api/backend/conversations/${conversation.id}/messages/message-2/feedback`,
      { method: "DELETE" },
    );
  });

  it("explains where to go when answer providers are not configured", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/health")) {
        return Response.json({ ...health, embeddings: "disabled", answers: "disabled" });
      }
      if (String(input).includes("/metadata/options")) {
        return Response.json(document.metadata);
      }
      if (String(input).includes("/conversations")) {
        return Response.json({ items: [], limit: 50, offset: 0 });
      }
      return Response.json({ items: [document], limit: 100, offset: 0 });
    }));

    render(<AssistantWorkspace />);

    expect(await screen.findByText("The assistant needs to be connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view settings/i })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("button", { name: "Ask assistant" })).toBeDisabled();
  });
});
