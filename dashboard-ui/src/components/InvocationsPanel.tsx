import { useEffect, useState } from "react";
import { relTime } from "../utils";
import { listInvocations, getInvocation } from "../api/client";
import type { InvocationRow, InvocationDetail } from "../api/types";

const fmtCost = (n: number | null) => (n == null ? "—" : "$" + n.toFixed(n < 0.01 ? 5 : 4));
const fmtMs = (n: number | null) => (n == null ? "—" : n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms");

function ratingChip(r: number | null) {
  if (r == null) return null;
  const color = r >= 0.66 ? "var(--success)" : r >= 0.33 ? "var(--warning)" : "var(--error)";
  return (
    <span className="chip mono" style={{ fontSize: 10, color, borderColor: color }}>
      ★ {r <= 1 ? (r * 5).toFixed(1) : r.toFixed(0)}
    </span>
  );
}

/** Reusable invocation list + trace detail. `order` drives whether it reads
 *  as a cost lens (most expensive first) or a behaviour lens (recent). */
export function InvocationsPanel({
  name,
  order = "recent",
  days = 30,
  emptyHint,
}: {
  name?: string;
  order?: "recent" | "cost";
  days?: number;
  emptyHint?: string;
}) {
  const [rows, setRows] = useState<InvocationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<InvocationDetail | null>(null);

  useEffect(() => {
    setLoading(true);
    setSel(null);
    listInvocations({ name, order, days, limit: 200 })
      .then((r) => setRows(r.invocations))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [name, order, days]);

  function open(id: number) {
    getInvocation(id).then(setSel).catch(() => setSel(null));
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: sel ? "1fr 1fr" : "1fr", gap: 14 }}>
      <div className="card" style={{ overflow: "hidden", height: "fit-content" }}>
        <table className="pr">
          <thead>
            <tr>
              <th>{order === "cost" ? "Call (priciest first)" : "Call"}</th>
              <th className="r">Tok i/o</th>
              <th className="r">Cost</th>
              <th className="r">Latency</th>
              <th>Rating</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={() => r.has_capture && open(r.id)}
                style={{ cursor: r.has_capture ? "pointer" : "default", opacity: r.has_capture ? 1 : 0.75 }}
              >
                <td>
                  <div style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                    <span className="mono" style={{ color: "var(--text-dim)" }}>#{r.id}</span>
                    {r.model && <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{r.model}</span>}
                    {r.has_capture && <span style={{ color: "var(--accent)", fontSize: 9 }}>●</span>}
                  </div>
                  {r.output_preview && (
                    <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 340 }}>
                      {r.output_preview}
                    </div>
                  )}
                </td>
                <td className="r mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>{r.tokens_in ?? "—"}/{r.tokens_out ?? "—"}</td>
                <td className="r mono" style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: order === "cost" ? 600 : 400 }}>{fmtCost(r.cost)}</td>
                <td className="r mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{fmtMs(r.latency_ms)}</td>
                <td>{ratingChip(r.rating) ?? <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>}</td>
                <td className="mono" style={{ fontSize: 11, color: "var(--secondary)" }}>{relTime(r.created_at)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: "center", padding: 36, color: "var(--muted)", fontSize: 12.5 }}>
                {loading ? "Loading…" : emptyHint || "No invocations in this window."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {sel && <TraceDetail detail={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

export function TraceDetail({ detail, onClose }: { detail: InvocationDetail; onClose: () => void }) {
  const m = detail.metadata as Record<string, number | string>;
  return (
    <div className="card" style={{ padding: 0, height: "fit-content", position: "sticky", top: 16 }}>
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{detail.prompt_name} <span className="mono" style={{ color: "var(--muted)", fontSize: 11 }}>#{detail.id}</span></div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2, fontFamily: "var(--font-mono)" }}>
            {m.model ?? ""} · {(m.tokens_in ?? "—")}/{(m.tokens_out ?? "—")} tok · {m.cost != null ? "$" + Number(m.cost).toFixed(5) : "—"} · {m.latency_ms != null ? Math.round(Number(m.latency_ms)) + "ms" : "—"}
          </div>
        </div>
        <button className="btn" onClick={onClose}>✕</button>
      </div>
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14, maxHeight: "72vh", overflow: "auto" }}>
        {detail.feedback.length > 0 && (
          <div>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, fontFamily: "var(--font-mono)" }}>Feedback</div>
            {detail.feedback.map((f, i) => (
              <div key={i} style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 6, padding: "7px 10px", background: "var(--bg-elev)", borderRadius: 6 }}>
                {f.rating != null && <span style={{ color: "var(--accent)", fontWeight: 600, marginRight: 8 }}>★ {f.rating}</span>}
                {f.comment || <span style={{ color: "var(--muted)" }}>(no comment)</span>}
                {f.source && <span style={{ color: "var(--muted)", marginLeft: 8, fontSize: 10.5 }}>via {f.source}</span>}
              </div>
            ))}
          </div>
        )}
        <Block label="Request" text={detail.input_text} color="var(--text-dim)" />
        <Block label="Response" text={detail.output_text} color="var(--text)" />
      </div>
    </div>
  );
}

function Block({ label, text, color }: { label: string; text: string | null; color: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, fontFamily: "var(--font-mono)" }}>{label}</div>
      <pre style={{ margin: 0, fontSize: 11.5, lineHeight: 1.55, whiteSpace: "pre-wrap", wordBreak: "break-word", color, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
        {text || "(not captured)"}
      </pre>
    </div>
  );
}
