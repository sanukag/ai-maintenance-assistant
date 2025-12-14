import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CredentialStatus } from "@/lib/api";
import { SettingsPanel } from "./settings-panel";

const health = {
  status: "ok", storage: "ok", ocr: "available" as const, ocr_engine: "tesseract", ocr_version: "5.5.0",
  visual_analysis: "available" as const, visual_analysis_model: "gpt-vision-test",
  embeddings: "enabled", embedding_model: "text-embedding-test", answers: "enabled", answer_model: "gpt-answer-test",
  vector_store: "qdrant", vector_index: "available", reranking: "enabled", rerank_model: "gpt-rerank-test",
};

const metrics = {
  started_at: "2026-07-14T10:00:00Z", uptime_seconds: 120, requests_total: 42, requests_in_flight: 1, errors_total: 0, routes: [],
  embedding_cache: { entries: 1200, hits: 48, maximum_entries: 10000 },
  sqlite: { journal_mode: "wal", synchronous: 1, busy_timeout_ms: 5000 },
};

const savedCredential = {
  name: "OPENAI_API_KEY" as const,
  label: "OpenAI API key",
  description: "Used for document embeddings, diagram understanding, evidence reranking and grounded answers.",
  used_by: ["Document embeddings", "Visual analysis", "Evidence reranking", "Grounded answers"],
  configured: true,
  source: "saved" as const,
  masked_value: "••••1234",
  updated_at: "2026-07-14T10:00:00Z",
  can_delete: true,
};

const missingCredential = {
  ...savedCredential,
  configured: false,
  source: "missing" as const,
  masked_value: null,
  updated_at: null,
  can_delete: false,
};

let credential: CredentialStatus = savedCredential;

describe("SettingsPanel", () => {
  beforeEach(() => {
    credential = savedCredential;
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const address = String(url);
      if (address.endsWith("/metrics")) return Response.json(metrics);
      if (address.includes("/credentials")) {
        if (init?.method === "PUT") credential = savedCredential;
        if (init?.method === "DELETE") credential = missingCredential;
        return Response.json(address.endsWith("/credentials") ? { items: [credential] } : credential);
      }
      return Response.json(health);
    }));
  });

  it("shows masked encrypted credentials without provider controls or raw secrets", async () => {
    render(<SettingsPanel />);

    expect(await screen.findByText("API connected")).toBeInTheDocument();
    expect(screen.getByText("OpenAI API key")).toBeInTheDocument();
    expect(screen.getByText("••••1234")).toBeInTheDocument();
    expect(screen.getByText("Encrypted locally")).toBeInTheDocument();
    expect(screen.getByText("text-embedding-test")).toBeInTheDocument();
    expect(screen.getByText("Qdrant HNSW index with SQLite fallback")).toBeInTheDocument();
    expect(screen.getByText("1,200 entries · 48 hits")).toBeInTheDocument();
    expect(screen.getByText("AMA_EMBEDDING_MODEL")).toBeInTheDocument();
    expect(screen.queryByText("AMA_EMBEDDING_PROVIDER")).not.toBeInTheDocument();
    expect(screen.queryByText("OPENAI_API_KEY")).not.toBeInTheDocument();
    expect(screen.queryByText(/replace-me|sk-project/)).not.toBeInTheDocument();
  });

  it("saves a new API key and never renders its complete value", async () => {
    credential = missingCredential;
    render(<SettingsPanel />);
    await screen.findByText("API connected");

    fireEvent.click(screen.getByRole("button", { name: "Add API key" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-project-secret-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Save API key" }));

    expect(await screen.findByText(/OpenAI API key saved/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue("sk-project-secret-1234")).not.toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/backend/credentials/OPENAI_API_KEY",
      expect.objectContaining({ method: "PUT" }),
    ));
  });

  it("edits and deletes a saved key with confirmation", async () => {
    render(<SettingsPanel />);
    await screen.findByText("••••1234");

    fireEvent.click(screen.getByRole("button", { name: "Edit key" }));
    expect(screen.getByLabelText("API key")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-project-edited-9876" } });
    fireEvent.click(screen.getByRole("button", { name: "Save API key" }));
    await screen.findByText(/OpenAI API key saved/);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(confirm).toHaveBeenCalled();
    expect(await screen.findByText("OpenAI API key deleted.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Add API key" })).toBeInTheDocument());
  });

  it("refreshes health, metrics and credential status together", async () => {
    render(<SettingsPanel />);
    await screen.findByText("API connected");

    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(6));
  });
});
