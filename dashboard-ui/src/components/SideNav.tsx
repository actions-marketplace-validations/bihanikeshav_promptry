import { NavLink, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { Wordmark } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  badge?: { n: number; tone: "error" | "warning" } | null;
}

const BASE_ITEMS: NavItem[] = [
  {
    to: "/",
    label: "Overview",
    icon: <path d="M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z" stroke="currentColor" strokeWidth="1.3" fill="none" />,
  },
  {
    to: "/evals",
    label: "Evals",
    icon: (
      <path
        d="M2 8.5L6 12l8-9"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    ),
  },
  {
    to: "/prompts",
    label: "Prompts",
    icon: (
      <path
        d="M4 2h6l2 2v10H4zM4 6h8M4 9h8M4 12h5"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
        strokeLinejoin="round"
      />
    ),
  },
  {
    to: "/models",
    label: "Models",
    icon: (
      <g stroke="currentColor" strokeWidth="1.3" fill="none">
        <circle cx="5" cy="5" r="2.5" />
        <circle cx="11" cy="11" r="2.5" />
        <path d="M5 7.5v3M7.5 5h3" />
      </g>
    ),
  },
  {
    to: "/cost",
    label: "Cost",
    icon: (
      <path
        d="M8 2v12M11 5a3 3 0 0 0-3-1.5c-2 0-2.5 1-2.5 2S6 7 8 7.5s2.5 1.5 2.5 2.5S9.5 12 8 12c-1.5 0-2.5-.5-3-1.5"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
        strokeLinecap="round"
      />
    ),
  },
  {
    to: "/playground",
    label: "Playground",
    icon: (
      <path
        d="M8 2l5 3v6l-5 3-5-3V5z M8 2v12M3 5l10 6M13 5L3 11"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
        strokeLinejoin="round"
      />
    ),
  },
];

export interface SideNavCounters {
  suites?: number;
  regressions?: number;
  drifting?: number;
}

export function SideNav({
  onCmdK,
  counters,
}: {
  onCmdK: () => void;
  counters: SideNavCounters;
}) {
  const location = useLocation();
  const items: NavItem[] = BASE_ITEMS.map((it) =>
    it.to === "/" && counters.regressions
      ? { ...it, badge: { n: counters.regressions, tone: "error" as const } }
      : it
  );

  return (
    <aside
      style={{
        width: 224,
        flexShrink: 0,
        position: "sticky",
        top: 0,
        height: "100vh",
        borderRight: "1px solid var(--border)",
        background: "var(--bg-elev)",
        display: "flex",
        flexDirection: "column",
        padding: "16px 12px",
        gap: 4,
      }}
    >
      <div
        style={{
          padding: "4px 6px 10px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Wordmark />
        <span className="chip mono" style={{ fontSize: 9.5, padding: "2px 6px" }}>
          v0.9.5
        </span>
      </div>

      <button
        onClick={onCmdK}
        className="btn"
        style={{ justifyContent: "flex-start", gap: 10, margin: "4px 0 10px" }}
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" style={{ color: "var(--secondary)" }}>
          <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.4" />
          <path d="m11 11 3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <span style={{ color: "var(--secondary)", fontSize: 12, flex: 1, textAlign: "left" }}>
          Search…
        </span>
        <span className="kbd">⌘K</span>
      </button>

      <div
        style={{
          fontSize: 10,
          color: "var(--muted)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          padding: "8px 8px 4px",
          fontFamily: "var(--font-mono)",
        }}
      >
        Workspace
      </div>

      {items.map((item) => {
        const active =
          location.pathname === item.to || (item.to !== "/" && location.pathname.startsWith(item.to));
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "8px 10px",
              borderRadius: 6,
              fontSize: 13,
              fontWeight: active ? 600 : 500,
              color: active ? "var(--text)" : "var(--secondary)",
              background: active ? "var(--surface)" : "transparent",
              border: "1px solid " + (active ? "var(--border)" : "transparent"),
              position: "relative",
              textDecoration: "none",
            }}
          >
            {active && (
              <span
                style={{
                  position: "absolute",
                  left: -12,
                  top: 8,
                  bottom: 8,
                  width: 2,
                  background: "var(--accent)",
                  borderRadius: 2,
                }}
              />
            )}
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              style={{ color: active ? "var(--accent)" : "var(--secondary)", flexShrink: 0 }}
            >
              {item.icon}
            </svg>
            <span style={{ flex: 1 }}>{item.label}</span>
            {item.badge && (
              <span
                className="mono"
                style={{
                  fontSize: 10,
                  padding: "1px 6px",
                  borderRadius: 999,
                  background: "var(--error-soft)",
                  color: "var(--error)",
                  fontWeight: 600,
                }}
              >
                {item.badge.n}
              </span>
            )}
          </NavLink>
        );
      })}

      <div
        style={{
          fontSize: 10,
          color: "var(--muted)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          padding: "14px 8px 4px",
          fontFamily: "var(--font-mono)",
        }}
      >
        Status
      </div>
      <div
        style={{
          padding: "8px 10px",
          fontSize: 11.5,
          fontFamily: "var(--font-mono)",
          color: "var(--secondary)",
          lineHeight: 1.7,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>suites</span>
          <span style={{ color: "var(--text)" }}>{counters.suites ?? "—"}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>regressions</span>
          <span style={{ color: "var(--error)" }}>{counters.regressions ?? 0}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>drifting</span>
          <span style={{ color: "var(--warning)" }}>{counters.drifting ?? 0}</span>
        </div>
      </div>

      <div
        style={{
          marginTop: "auto",
          padding: "8px 10px",
          borderTop: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span className="live-dot" />
        <span className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
          localhost:{new URLSearchParams(window.location.search).get("port") || "8420"}
        </span>
      </div>
    </aside>
  );
}
