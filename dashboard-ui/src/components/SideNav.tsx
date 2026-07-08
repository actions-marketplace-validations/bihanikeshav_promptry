import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useState, type ReactNode } from "react";
import { Wordmark } from "./ui";
import { backendHost } from "../api/client";

/** Footer status: pings /api/health and shows the real host + connection. */
function ConnectionStatus() {
  const [state, setState] = useState<"checking" | "connected" | "offline">("checking");
  const [version, setVersion] = useState<string>("");
  useEffect(() => {
    let alive = true;
    const ping = () =>
      fetch("/api/health")
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((d) => { if (alive) { setState("connected"); setVersion(d.version || ""); } })
        .catch(() => { if (alive) setState("offline"); });
    ping();
    const t = setInterval(ping, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const color = state === "connected" ? "var(--success)" : state === "offline" ? "var(--error)" : "var(--muted)";
  return (
    <div style={{ marginTop: "auto", padding: "8px 10px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 7, height: 7, borderRadius: 999, background: color, boxShadow: `0 0 0 3px color-mix(in oklch, ${color} 18%, transparent)` }} />
      <span className="mono" style={{ fontSize: 11, color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {state === "offline" ? "disconnected" : backendHost()}
      </span>
      {state === "connected" && version && (
        <span className="mono" style={{ fontSize: 10, color: "var(--muted)", marginLeft: "auto" }}>v{version}</span>
      )}
    </div>
  );
}

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
    to: "/suites/new",
    label: "New suite",
    icon: (
      <g stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinecap="round">
        <rect x="2.5" y="2.5" width="11" height="11" rx="2.5" />
        <path d="M8 5.5v5M5.5 8h5" />
      </g>
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
    to: "/cache",
    label: "Cache optimization",
    icon: (
      <g stroke="currentColor" strokeWidth="1.3" fill="none" strokeLinejoin="round">
        <rect x="2.5" y="2.5" width="7" height="7" rx="1.5" />
        <rect x="6.5" y="6.5" width="7" height="7" rx="1.5" />
      </g>
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
    to: "/feedback",
    label: "Feedback",
    icon: (
      <path
        d="M2 3.5h12v7H6l-3 2.5V10.5H2z"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
        strokeLinejoin="round"
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
  {
    to: "/settings",
    label: "Settings",
    icon: (
      <g stroke="currentColor" strokeWidth="1.3" fill="none">
        <circle cx="8" cy="8" r="2.2" />
        <path d="M8 1.5v1.8M8 12.7v1.8M14.5 8h-1.8M3.3 8H1.5M12.6 3.4l-1.3 1.3M4.7 11.3l-1.3 1.3M12.6 12.6l-1.3-1.3M4.7 4.7L3.4 3.4" strokeLinecap="round" />
      </g>
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
          v1.0.0
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

      <ConnectionStatus />
    </aside>
  );
}
