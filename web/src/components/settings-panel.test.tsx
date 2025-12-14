import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "./settings-panel";

const health = {
  status: "ok",
  storage: "ok",
  ocr: "available" as const,
  ocr_engine: "tesseract",
  ocr_version: "5.5.0",
  visual_analysis: "available" as const,
  visual_analysis_model: "gpt-vision-test",
  embeddings: "enabled",
  embedding_model: "text-embedding-test",
  answers: "enabled",
  answer_model: "gpt-answer-test",
  vector_store: "qdrant",
  vector_index: "available",
  reranking: "enabled",
  rerank_model: "gpt-rerank-test",
};

const metrics = {
  started_at: "2026-07-14T10:00:00Z",
  uptime_seconds: 120,
  requests_total: 42,
  requests_in_flight: 1,
  errors_total: 0,
  routes: [],
  embedding_cache: { entries: 1200, hits: 48, maximum_entries: 10000 },
  sqlite: { journal_mode: "wal", synchronous: 1, busy_timeout_ms: 5000 },
};

describe("SettingsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) =>
      Response.json(String(url).endsWith("/metrics") ? metrics : health)
    ));
  });

  it("separates live system and developer information from the worker workspace", async () => {
    render(<SettingsPanel />);

    expect(await screen.findByText("API connected")).toBeInTheDocument();
    expect(screen.getByText("text-embedding-test")).toBeInTheDocument();
    expect(screen.getByText("tesseract 5.5.0")).toBeInTheDocument();
    expect(screen.getByText("gpt-vision-test")).toBeInTheDocument();
    expect(screen.getByText("gpt-answer-test")).toBeInTheDocument();
    expect(screen.getByText("gpt-rerank-test")).toBeInTheDocument();
    expect(screen.getByText("Qdrant HNSW index with SQLite fallback")).toBeInTheDocument();
    expect(screen.getByText("Next.js 16 · App Router")).toBeInTheDocument();
    expect(screen.getByText("OPENAI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("AMA_VISUAL_ANALYSIS_PROVIDER")).toBeInTheDocument();
    expect(screen.getByText("AMA_RERANK_PROVIDER")).toBeInTheDocument();
    expect(screen.getByText("Service status")).toBeInTheDocument();
    expect(screen.getByText("1,200 entries · 48 hits")).toBeInTheDocument();
    expect(screen.getByText("5,000 ms")).toBeInTheDocument();
    expect(screen.getByText("AMA_EMBEDDING_CACHE_MAX_ENTRIES")).toBeInTheDocument();
    expect(screen.queryByText("What stays local?")).not.toBeInTheDocument();
    expect(screen.queryByText(/replace-me|sk-/)).not.toBeInTheDocument();
  });

  it("allows the status to be refreshed", async () => {
    render(<SettingsPanel />);
    await screen.findByText("API connected");

    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));

    expect(fetch).toHaveBeenCalledTimes(4);
  });
});
