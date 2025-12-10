import { describe, expect, it } from "vitest";
import { formatFileSize, readJson, sourceLocation } from "./api";

describe("API presentation helpers", () => {
  it("turns structured API failures into readable messages", async () => {
    const response = Response.json(
      { error: { code: "answers_disabled", message: "Grounded answers are not configured" } },
      { status: 503 },
    );

    await expect(readJson(response)).rejects.toThrow("Grounded answers are not configured");
  });

  it("formats file sizes and source locations for workers", () => {
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(sourceLocation({ page_start: 4, page_end: 5 } as never)).toBe("Pages 4–5");
    expect(sourceLocation({ page_start: null, headings: ["Isolation"], line_start: null } as never)).toBe("Isolation");
  });
});
