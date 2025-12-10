"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Icon, type IconName } from "@/components/icons";

const navigation: { href: string; label: string; caption: string; icon: IconName }[] = [
  { href: "/", label: "Assistant", caption: "Ask a maintenance question", icon: "spark" },
  { href: "/manuals", label: "Manuals", caption: "Manage the knowledge base", icon: "manual" },
  { href: "/settings", label: "Settings", caption: "System and developer details", icon: "settings" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <div className="app-shell">
      <button
        className="mobile-menu-button"
        type="button"
        aria-label="Open navigation"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Icon name="menu" />
      </button>
      {open && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <Link className="brand" href="/" onClick={() => setOpen(false)}>
            <span className="brand-mark"><Icon name="spark" /></span>
            <span><strong>Maintenance</strong><small>Assistant</small></span>
          </Link>
          <button className="sidebar-close" type="button" aria-label="Close navigation" onClick={() => setOpen(false)}>
            <Icon name="close" />
          </button>
        </div>

        <nav className="primary-nav" aria-label="Main navigation">
          <p className="nav-label">Workspace</p>
          {navigation.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-item ${active ? "nav-item-active" : ""}`}
                aria-current={active ? "page" : undefined}
                onClick={() => setOpen(false)}
              >
                <span className="nav-icon"><Icon name={item.icon} /></span>
                <span><strong>{item.label}</strong><small>{item.caption}</small></span>
                {active && <span className="nav-active-dot" />}
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="local-data-card">
            <span className="local-data-icon"><Icon name="shield" /></span>
            <div><strong>Local knowledge base</strong><p>Manuals and source records stay on this machine.</p></div>
          </div>
          <div className="workspace-user">
            <span className="user-avatar">MW</span>
            <span><strong>Maintenance team</strong><small>Local workspace</small></span>
          </div>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
