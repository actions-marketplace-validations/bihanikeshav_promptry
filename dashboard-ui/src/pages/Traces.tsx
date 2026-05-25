import { useEffect, useState } from "react";
import { PageHeader, Select } from "../components/ui";
import { relTime } from "../utils";
import { listInvocations, getInvocation } from "../api/client";
import type { InvocationRow, InvocationDetail } from "../api/types";

export default function Traces() {
  const [days, setDays] = useState(7);
  const [capturedOnly, setCapturedOnly] = useState(false);
  const [rows, setRows] = useState<InvocationRow[]>([]);
  const [selected, setSelected] = useState<InvocationDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    listInvocations({ days, limit: 200, capturedOnly })
      .then((r) => setRows(r.invocations))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [days, capturedOnly]);

  function open(id: number) {
    getInvocation(id).then(setSelected).catch(() => setSelected(null));
  }

  const fmtCost = (n: number | null) => (n == null ? "—" : "$" + n.toFixed(n < 0.01 ? 5 : 4));
  const fmtMs = (n: number | null) => (n == null ? "—" : n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms");

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · traces"
        title="Traces"
        description="Per-call invocations from the ledger. Rows with captured text open a full request/response view."
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              className="btn"
              onClick={() => setCapturedOnly((c) => !c)}
              style={{ background: capturedOnly ? "var(--accent)" : undefined, color: capturedOnly ? "var(--bg)" : undefined }}
            >
              {capturedOnly ? "✓ " : ""}Captured only
            </button>
            <Select
              value={days}
              onChange={setDays}
              options={[
                { value: 1, label: "Last 24h" },
                { value: 7, label: "Last 7 days" },
                { value: 30, label: "Last 30 days" },
              ]}
            />
          </div>
        }
      />

      <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 1fr" : "1fr", gap: 16 }}>
        <div className="card" style={{ overflow: "hidden", height: "fit-content" }}>
          <table className="pr">
            <thead>
              <tr>
                <th>Prompt</th><th>Model</th><th className="r">Tok in/out</th>
                <th className="r">Cost</th><th className="r">Latency</th><th>When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => r.has_capture && open(r.id)}
                  style={{ cursor: r.has_capture ? "pointer" : "default", opacity: r.has_capture ? 1 : 0.7 }}
                >
                  <td>
                    <div style={{ fontWeight: 600, fontSize: 12.5 }}>
                      {r.prompt_name}
                      {r.has_capture && <span style={{ color: "var(--accent)", marginLeft: 6, fontSize: 10 }}>●</span>}
                    </div>
                    {r.output_preview && (
                      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 360 }}>
                        {r.output_preview}
                      </div>
                    )}
                  </td>
                  <td className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>{r.model ?? "—"}</td>
                  <td className="r mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
                    {r.tokens_in ?? "—"}/{r.tokens_out ?? "—"}
                  </td>
                  <td className="r mono" style={{ fontSize: 11.5, color: "var(--accent)" }}>{fmtCost(r.cost)}</td>
                  <td className="r mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{fmtMs(r.latency_ms)}</td>
                  <td className="mono" style={{ fontSize: 11, color: "var(--secondary)" }}>{relTime(r.created_at)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
                  {loading ? "Loading…" : capturedOnly ? "No captured traces. Set PROMPTRY_CAPTURE=1 in the app to capture request/response text." : "No invocations in this window."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {selected && (
          <div className="card" style={{ padding: 0, height: "fit-content", position: "sticky", top: 16 }}>
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{selected.prompt_name}</div>
              <button className="btn" onClick={() => setSelected(null)}>✕</button>
            </div>
            <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14, maxHeight: "70vh", overflow: "auto" }}>
              <div>
                <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, fontFamily: "var(--font-mono)" }}>Request</div>
                <pre style={{ margin: 0, fontSize: 11.5, lineHeight: 1.55, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--text-dim)", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
                  {selected.input_text || "(not captured)"}
                </pre>
              </div>
              <div>
                <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, fontFamily: "var(--font-mono)" }}>Response</div>
                <pre style={{ margin: 0, fontSize: 11.5, lineHeight: 1.55, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--text)", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
                  {selected.output_text || "(not captured)"}
                </pre>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
