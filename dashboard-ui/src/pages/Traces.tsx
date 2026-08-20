import { useEffect, useState } from "react";
import { PageHeader } from "../components/ui";
import { listTraces, getTrace, type TraceSummary, type TraceStep } from "../api/client";

function usd(n: number | null | undefined): string {
  if (n == null) return "—";
  return "$" + n.toFixed(n < 0.01 ? 5 : 4);
}

export default function Traces() {
  const [traces, setTraces] = useState<TraceSummary[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [totalCost, setTotalCost] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    listTraces(7)
      .then((r) => setTraces(r.traces))
      .catch((e) => setErr(e instanceof Error ? e.message : "failed to load traces"));
  }, []);

  function expand(id: string) {
    if (open === id) {
      setOpen(null);
      return;
    }
    setOpen(id);
    setSteps([]);
    getTrace(id).then((r) => {
      setSteps(r.steps);
      setTotalCost(r.total_cost);
    });
  }

  const maxStepCost = Math.max(0.000001, ...steps.map((s) => s.cost || 0));

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · alpha"
        title="Call traces"
        description="Group a run's LLM calls with `with promptry.trace('agent'):` (Python) or trace() (JS) to see which step of your agent burned the tokens — a cost waterfall per trace."
      />

      {err && (
        <div className="card" style={{ padding: "10px 14px", marginBottom: 16, background: "var(--error-soft)", color: "var(--error)", fontSize: 12.5 }}>
          {err}
        </div>
      )}

      {traces && traces.length === 0 && (
        <div className="card" style={{ padding: 20, color: "var(--muted)", fontSize: 13 }}>
          No traces yet. Wrap a run in a trace to see its per-step cost:
          <pre className="mono" style={{ marginTop: 10, fontSize: 12, color: "var(--secondary)" }}>
{`with promptry.trace("checkout_agent"):
    client.chat.completions.create(...)   # step 1
    client.chat.completions.create(...)   # step 2`}
          </pre>
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        {traces?.map((t) => (
          <div key={t.trace_id} style={{ borderBottom: "1px solid var(--border)" }}>
            <div
              onClick={() => expand(t.trace_id)}
              style={{ display: "flex", alignItems: "center", gap: 14, padding: "12px 16px", cursor: "pointer" }}
            >
              <span style={{ color: "var(--muted)", fontSize: 11, width: 12 }}>{open === t.trace_id ? "▾" : "▸"}</span>
              <span className="mono" style={{ fontSize: 12, color: "var(--text)", flex: 1 }}>{t.trace_id}</span>
              <span style={{ fontSize: 12, color: "var(--secondary)" }}>{t.steps} steps</span>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>{t.tokens_in + t.tokens_out} tok</span>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--accent-bright)", width: 78, textAlign: "right" }}>{usd(t.cost)}</span>
              <span style={{ fontSize: 11, color: "var(--muted)", width: 150, textAlign: "right" }}>
                {new Date(t.started_at + "Z").toLocaleString()}
              </span>
            </div>

            {open === t.trace_id && (
              <div style={{ padding: "4px 16px 16px 40px", background: "var(--bg-elev)" }}>
                {steps.map((s, i) => {
                  const cost = s.cost || 0;
                  const pct = Math.round((cost / maxStepCost) * 100);
                  return (
                    <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "6px 0" }}>
                      <span style={{ width: 18, color: "var(--muted)", fontSize: 11 }}>{i + 1}</span>
                      <span className="mono" style={{ width: 130, fontSize: 11.5, color: "var(--secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.prompt_name}>
                        {s.prompt_name}
                      </span>
                      <span style={{ width: 90, fontSize: 11, color: "var(--muted)" }}>{s.model || "—"}</span>
                      <div style={{ flex: 1, height: 8, background: "var(--surface)", borderRadius: 4, overflow: "hidden" }}>
                        <div style={{ width: `${pct}%`, height: "100%", background: s.status === "error" ? "var(--error)" : "var(--accent)" }} />
                      </div>
                      <span style={{ width: 80, textAlign: "right", fontSize: 11.5, color: "var(--text)" }}>{usd(s.cost)}</span>
                      <span style={{ width: 70, textAlign: "right", fontSize: 11, color: "var(--muted)" }}>
                        {(s.tokens_in || 0) + (s.tokens_out || 0)} tok
                      </span>
                    </div>
                  );
                })}
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)", display: "flex", justifyContent: "flex-end", gap: 8, fontSize: 12 }}>
                  <span style={{ color: "var(--muted)" }}>trace total</span>
                  <span style={{ fontWeight: 600, color: "var(--accent-bright)" }}>{usd(totalCost)}</span>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
