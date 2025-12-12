import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

const recent = {
  id: "recent-chat",
  title: "Inspect compressor vibration",
  created_at: "2026-07-14T10:00:00Z",
  updated_at: "2026-07-14T10:30:00Z",
  message_count: 4,
};
const older = {
  id: "older-chat",
  title: "Replace the pump seal",
  created_at: "2026-07-13T10:00:00Z",
  updated_at: "2026-07-13T10:30:00Z",
  message_count: 2,
};

describe("AppShell", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ items: [recent, older], limit: 100, offset: 0 })));
  });

  it("places recent chats below Assistant and keeps administration navigation at the bottom", async () => {
    render(<AppShell><p>Workspace</p></AppShell>);

    const chats = await screen.findAllByText(/Inspect compressor vibration|Replace the pump seal/);
    expect(chats.map((item) => item.textContent)).toEqual([recent.title, older.title]);
    expect(screen.getByRole("button", { name: /assistant.*maintenance workspace/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manuals.*document library/i })).toHaveAttribute("href", "/manuals");
    expect(screen.getByRole("link", { name: /settings.*system information/i })).toHaveAttribute("href", "/settings");
  });

  it("opens and deletes chats from the sidebar", async () => {
    let deleted = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      if (options?.method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      return Response.json({ items: deleted ? [older] : [recent, older], limit: 100, offset: 0 });
    }));
    const selected = vi.fn();
    window.addEventListener("assistant-conversation-selected", selected);
    render(<AppShell><p>Workspace</p></AppShell>);

    fireEvent.click(await screen.findByRole("button", { name: /inspect compressor vibration14 jul/i }));
    expect(window.location.search).toBe("?conversation=recent-chat");
    expect(selected).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: `Delete ${recent.title}` }));
    await waitFor(() => expect(screen.queryByText(recent.title)).not.toBeInTheDocument());
    expect(confirm).toHaveBeenCalled();
    window.removeEventListener("assistant-conversation-selected", selected);
  });
});
