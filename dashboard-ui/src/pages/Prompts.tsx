import { useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { PageHeader } from "../components/ui";
import type { LayoutContext } from "../components/Layout";

export default function Prompts() {
  const { prompts } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();
  const [q, setQ] = useState("");

  const filtered = prompts.filter(
    (p) => !q || p.name.toLowerCase().includes(q.toLowerCase())
  );

  // Group by module — the segment before the first dot (agent, chunking,
  // summary, …). Names without a dot fall under "other".
  const groups = (() => {
    const m = new Map<string, typeof filtered>();
    for (const p of filtered) {
      const mod = p.name.includes(".") ? p.name.split(".")[0] : "other";
      if (!m.has(mod)) m.set(mod, []);
      m.get(mod)!.push(p);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  })();

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · prompts"
        title="Prompt Registry"
        description="Every versioned prompt tracked by promptry. Click one to inspect history and diffs."
        actions={
          <input
            className="inp"
            placeholder="filter prompts…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ minWidth: 240 }}
          />
        }
      />

      {filtered.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
          No prompts found.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        {groups.map(([mod, items]) => (
          <div key={mod}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 8,
                padding: "0 2px",
              }}
            >
              <span
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  color: "var(--text)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {mod}
              </span>
              <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                {items.length}
              </span>
              <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
            </div>
            <div className="card" style={{ overflow: "hidden" }}>
              <table className="pr">
                <tbody>
                  {items.map((p) => {
                    // Show the part after the module prefix as the primary label.
                    const sub = p.name.includes(".")
                      ? p.name.slice(p.name.indexOf(".") + 1)
                      : p.name;
                    return (
                      <tr key={p.name} onClick={() => navigate(`/prompts/${encodeURIComponent(p.name)}`)}>
                        <td>
                          <div style={{ fontSize: 13.5, color: "var(--text)", fontWeight: 600 }}>
                            <span style={{ color: "var(--muted)" }}>{mod}.</span>
                            {sub}
                          </div>
                        </td>
                        <td className="mono" style={{ color: "var(--text-dim)", width: 70 }}>
                          v{p.latest_version}
                        </td>
                        <td style={{ width: 200 }}>
                          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                            {p.tags.length === 0 ? (
                              <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>
                            ) : (
                              p.tags.map((t) => (
                                <span
                                  key={t}
                                  className="chip mono"
                                  style={{
                                    fontSize: 10,
                                    color: t === "critical" ? "var(--accent)" : "var(--text-dim)",
                                    borderColor: t === "critical" ? "var(--accent-line)" : "var(--border)",
                                    background: t === "critical" ? "var(--accent-soft)" : "rgba(255,255,255,0.02)",
                                  }}
                                >
                                  {t}
                                </span>
                              ))
                            )}
                          </div>
                        </td>
                        <td className="r" style={{ width: 30 }}>
                          <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>›</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
