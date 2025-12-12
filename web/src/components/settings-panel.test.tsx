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
};

describe("SettingsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(health)));
  });

  it("separates live system and developer information from the worker workspace", async () => {
    render(<SettingsPanel />);

    expect(await screen.findByText("All local services operational")).toBeInTheDocument();
    expect(screen.getByText("text-embedding-test")).toBeInTheDocument();
    expect(screen.getByText("tesseract 5.5.0")).toBeInTheDocument();
    expect(screen.getByText("gpt-vision-test")).toBeInTheDocument();
    expect(screen.getByText("gpt-answer-test")).toBeInTheDocument();
    expect(screen.getByText("Next.js 16 · App Router")).toBeInTheDocument();
    expect(screen.getByText("OPENAI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("AMA_VISUAL_ANALYSIS_PROVIDER")).toBeInTheDocument();
    expect(screen.getByText("Conversation history")).toBeInTheDocument();
    expect(screen.queryByText(/replace-me|sk-/)).not.toBeInTheDocument();
  });

  it("allows the status to be refreshed", async () => {
    render(<SettingsPanel />);
    await screen.findByText("All local services operational");

    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));

    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
