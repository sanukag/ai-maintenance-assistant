import type { SVGProps } from "react";

export type IconName =
  | "spark"
  | "manual"
  | "settings"
  | "send"
  | "upload"
  | "file"
  | "check"
  | "chevron"
  | "shield"
  | "server"
  | "database"
  | "refresh"
  | "menu"
  | "close"
  | "arrow";

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };

  const paths: Record<IconName, React.ReactNode> = {
    spark: <><path d="m12 3 1.35 4.08a5.6 5.6 0 0 0 3.57 3.57L21 12l-4.08 1.35a5.6 5.6 0 0 0-3.57 3.57L12 21l-1.35-4.08a5.6 5.6 0 0 0-3.57-3.57L3 12l4.08-1.35a5.6 5.6 0 0 0 3.57-3.57L12 3Z" /></>,
    manual: <><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v17H7.5A2.5 2.5 0 0 0 5 21.5V4.5Z" /><path d="M5 19h15M9 6h7M9 10h7" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.09A1.7 1.7 0 0 0 8.56 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.09A1.7 1.7 0 0 0 4.6 8.56a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.09A1.7 1.7 0 0 0 15.44 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.15.37.37.7.66.98.3.28.68.43 1.09.43H21v4h-.09A1.7 1.7 0 0 0 19.4 15Z" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4 20-7Z" /><path d="M22 2 11 13" /></>,
    upload: <><path d="M12 16V3M7 8l5-5 5 5" /><path d="M20 15v5H4v-5" /></>,
    file: <><path d="M6 2h8l4 4v16H6z" /><path d="M14 2v5h5M9 12h6M9 16h6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></>,
    server: <><rect x="3" y="3" width="18" height="7" rx="2" /><rect x="3" y="14" width="18" height="7" rx="2" /><path d="M7 6.5h.01M7 17.5h.01" /></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v7c0 1.66 3.58 3 8 3s8-1.34 8-3V5" /><path d="M4 12v7c0 1.66 3.58 3 8 3s8-1.34 8-3v-7" /></>,
    refresh: <><path d="M20 6v5h-5" /><path d="M4 18v-5h5" /><path d="M18.4 9A7 7 0 0 0 6.1 6.1L4 11M20 13l-2.1 4.9A7 7 0 0 1 5.6 15" /></>,
    menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  };

  return <svg {...common}>{paths[name]}</svg>;
}
