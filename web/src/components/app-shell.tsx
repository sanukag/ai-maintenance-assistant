"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { type ConversationList, type ConversationSummary, readJson } from "@/lib/api";

const HISTORY_PAGE_SIZE = 100;

function historyDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(new Date(value));
}

function currentConversationId() {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("conversation");
}

async function fetchAllConversations() {
  const collected: ConversationSummary[] = [];
  let offset = 0;
  while (true) {
    const response = await fetch(
      `/api/backend/conversations?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`,
      { cache: "no-store" },
    );
    const page = await readJson<ConversationList>(response);
    collected.push(...page.items);
    if (page.items.length < HISTORY_PAGE_SIZE) return collected;
    offset += page.items.length;
  }
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(currentConversationId);

  const loadConversations = useCallback(async () => {
    try {
      const collected = await fetchAllConversations();
      setHistoryError(false);
      setConversations(collected);
      setActiveConversationId(currentConversationId());
    } catch {
      setHistoryError(true);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    fetchAllConversations()
      .then((items) => {
        if (!active) return;
        setConversations(items);
        setActiveConversationId(currentConversationId());
      })
      .catch(() => { if (active) setHistoryError(true); })
      .finally(() => { if (active) setHistoryLoading(false); });
    const refresh = () => void loadConversations();
    const trackSelection = (event: Event) => {
      setActiveConversationId((event as CustomEvent<{ conversationId: string | null }>).detail.conversationId);
    };
    window.addEventListener("conversation-history-updated", refresh);
    window.addEventListener("assistant-conversation-selected", trackSelection);
    return () => {
      active = false;
      window.removeEventListener("conversation-history-updated", refresh);
      window.removeEventListener("assistant-conversation-selected", trackSelection);
    };
  }, [loadConversations]);

  function selectConversation(conversationId: string | null) {
    const destination = conversationId ? `/?conversation=${encodeURIComponent(conversationId)}` : "/";
    window.history.pushState({}, "", destination);
    setActiveConversationId(conversationId);
    window.dispatchEvent(new CustomEvent("assistant-conversation-selected", { detail: { conversationId } }));
    setOpen(false);
  }

  async function deleteConversation(conversation: ConversationSummary) {
    if (!window.confirm(`Delete “${conversation.title}” and all of its messages?`)) return;
    try {
      const response = await fetch(`/api/backend/conversations/${conversation.id}`, { method: "DELETE" });
      if (!response.ok) await readJson(response);
      if (activeConversationId === conversation.id) selectConversation(null);
      await loadConversations();
    } catch {
      setHistoryError(true);
    }
  }

  return (
    <div className="app-shell">
      <button className="mobile-menu-button" type="button" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(true)}>
        <Icon name="menu" />
      </button>
      {open && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <Link className="brand" href="/" onClick={() => setOpen(false)}>
            <span className="brand-mark"><Icon name="spark" /></span>
            <span><strong>Maintenance</strong><small>Assistant</small></span>
          </Link>
          <button className="sidebar-close" type="button" aria-label="Close navigation" onClick={() => setOpen(false)}><Icon name="close" /></button>
        </div>

        <nav className="primary-nav" aria-label="Main navigation">
          <button className={`nav-item nav-item-button ${pathname === "/" ? "nav-item-active" : ""}`} type="button" onClick={() => selectConversation(null)} aria-current={pathname === "/" ? "page" : undefined}>
            <span className="nav-icon"><Icon name="spark" /></span>
            <span><strong>Assistant</strong><small>Maintenance workspace</small></span>
            {pathname === "/" && <span className="nav-active-dot" />}
          </button>
        </nav>

        <section className="sidebar-history" aria-label="Previous conversations">
          <div className="sidebar-section-heading"><span>Previous chats</span><button type="button" onClick={() => selectConversation(null)}>New</button></div>
          <div className="sidebar-history-list">
            {historyLoading ? <p className="sidebar-history-state">Loading chats…</p> : historyError ? (
              <button className="sidebar-history-retry" type="button" onClick={loadConversations}>Retry loading chats</button>
            ) : conversations.length ? conversations.map((conversation) => (
              <div className={`sidebar-history-item ${activeConversationId === conversation.id ? "sidebar-history-item-active" : ""}`} key={conversation.id}>
                <button type="button" className="sidebar-history-open" onClick={() => selectConversation(conversation.id)}>
                  <strong>{conversation.title}</strong><span>{historyDate(conversation.updated_at)} · {conversation.message_count} messages</span>
                </button>
                <button type="button" className="sidebar-history-delete" aria-label={`Delete ${conversation.title}`} onClick={() => deleteConversation(conversation)}><Icon name="trash" /></button>
              </div>
            )) : <p className="sidebar-history-state">No previous chats</p>}
          </div>
        </section>

        <nav className="secondary-nav" aria-label="Library and settings">
          <Link className={`nav-item ${pathname.startsWith("/manuals") ? "nav-item-active" : ""}`} href="/manuals" onClick={() => setOpen(false)}>
            <span className="nav-icon"><Icon name="manual" /></span><span><strong>Manuals</strong><small>Document library</small></span>{pathname.startsWith("/manuals") && <span className="nav-active-dot" />}
          </Link>
          <Link className={`nav-item ${pathname.startsWith("/settings") ? "nav-item-active" : ""}`} href="/settings" onClick={() => setOpen(false)}>
            <span className="nav-icon"><Icon name="settings" /></span><span><strong>Settings</strong><small>System information</small></span>{pathname.startsWith("/settings") && <span className="nav-active-dot" />}
          </Link>
        </nav>

        <div className="workspace-user"><span className="user-avatar">MW</span><span><strong>Maintenance team</strong><small>Local workspace</small></span></div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
