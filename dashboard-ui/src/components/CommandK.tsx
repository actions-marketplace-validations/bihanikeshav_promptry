import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { SuiteSummary, PromptSummary } from "../api/types";

interface SearchItem {
  kind: "Suite" | "Prompt" | "Page";
  name: string;
  sub: string;
  path: string;
}

const PAGES: SearchItem[] = [
  { kind: "Page", name: "Overview", sub: "Eval suites dashboard", path: "/" },
  { kind: "Page", name: "Prompts", sub: "Prompt registry", path: "/prompts" },
  { kind: "Page", name: "Models", sub: "Model comparison", path: "/models" },
  { kind: "Page", name: "Cost", sub: "Cost tracking", path: "/cost" },
  { kind: "Page", name: "Playground", sub: "Assertion playground", path: "/playground" },
];

export function CommandK({
  open,
  onClose,
  suites,
  prompts,
}: {
  open: boolean;
  onClose: () => void;
  suites: SuiteSummary[];
  prompts: PromptSummary[];
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo(() => {
    const all: SearchItem[] = [];
    suites.forEach((s) =>
      all.push({
        kind: "Suite",
        name: s.name,
        sub: `${s.model_version ?? "—"} · prompt v${s.prompt_version ?? "—"}`,
        path: `/suite/${encodeURIComponent(s.name)}`,
      })
    );
    prompts.forEach((p) =>
      all.push({
        kind: "Prompt",
        name: p.name,
        sub: `v${p.latest_version}`,
        path: `/prompts/${encodeURIComponent(p.name)}`,
      })
    );
    PAGES.forEach((p) => all.push(p));
    const query = q.trim().toLowerCase();
    return all.filter((i) => !query || (i.name + " " + i.sub).toLowerCase().includes(query));
  }, [q, suites, prompts]);

  useEffect(() => {
    setActive(0);
  }, [q, open]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 10);
  }, [open]);

  if (!open) return null;

  const go = (i: number) => {
    const it = items[i];
    if (!it) return;
    navigate(it.path);
    onClose();
  };

  return (
    <div
      className="pr-dialog-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="pr-dialog" role="dialog" aria-modal="true">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "14px 16px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ flexShrink: 0, color: "var(--secondary)" }}>
            <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.4" />
            <path d="m11 11 3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(items.length - 1, a + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(0, a - 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                go(active);
              } else if (e.key === "Escape") {
                onClose();
              }
            }}
            placeholder="Search suites, prompts, pages…"
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text)",
              fontSize: 14,
              fontFamily: "var(--font-ui)",
            }}
          />
          <span className="kbd">esc</span>
        </div>
        <div style={{ maxHeight: 340, overflow: "auto" }}>
          {items.length === 0 && (
            <div style={{ padding: 24, color: "var(--muted)", fontSize: 13, textAlign: "center" }}>
              No matches.
            </div>
          )}
          {items.map((it, i) => (
            <div
              key={i}
              onMouseEnter={() => setActive(i)}
              onClick={() => go(i)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 16px",
                background: active === i ? "var(--surface-2)" : "transparent",
                borderLeft: active === i ? "2px solid var(--accent)" : "2px solid transparent",
                cursor: "pointer",
              }}
            >
              <span
                className="chip mono"
                style={{ minWidth: 58, justifyContent: "center", fontSize: 10, textTransform: "uppercase" }}
              >
                {it.kind}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    color: "var(--text)",
                    fontWeight: 500,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {it.name}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--muted)",
                    fontFamily: "var(--font-mono)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {it.sub}
                </div>
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>↵</span>
            </div>
          ))}
        </div>
        <div
          style={{
            display: "flex",
            gap: 14,
            padding: "8px 14px",
            borderTop: "1px solid var(--border)",
            fontSize: 10.5,
            color: "var(--muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          <span>
            <span className="kbd">↑↓</span> navigate
          </span>
          <span>
            <span className="kbd">↵</span> open
          </span>
          <span style={{ marginLeft: "auto" }}>
            promptry <span style={{ color: "var(--accent)" }}>·</span> search
          </span>
        </div>
      </div>
    </div>
  );
}
