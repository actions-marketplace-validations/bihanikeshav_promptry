import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { KPI, PageHeader, Sparkline, StatusPill } from "../components/ui";
import { pct, relTime, usd, formatTokens, scoreColor } from "../utils";
import { getCostData, listBudgets, listInvocations } from "../api/client";
import type { LayoutContext } from "../components/Layout";
import type { CostResponse, BudgetStatus, InvocationRow } from "../api/types";

/* one-line clamp that fades instead of cutting with an ellipsis */
const fade: React.CSSProperties = {
  display: "block", maxWidth: 150, overflow: "hidden", whiteSpace: "nowrap",
  maskImage: "linear-gradient(90deg,#000 78%,transparent)",
  WebkitMaskImage: "linear-gradient(90deg,#000 78%,transparent)",
};

function SectionCard({ title, sub, action, children }: { title: string; sub?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "13px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
          {sub && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{sub}</div>}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

const ratingColor = (r: number | null) =>
  r == null ? "var(--border-strong)" : r >= 0.8 ? "var(--success)" : r < 0.5 ? "var(--error)" : "var(--warning)";

export default function Overview() {
  const { suites } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();
  const [cost, setCost] = useState<CostResponse | null>(null);
  const [budgets, setBudgets] = useState<BudgetStatus[]>([]);
  const [recent, setRecent] = useState<InvocationRow[]>([]);

  useEffect(() => {
    getCostData(30).then(setCost).catch(() => setCost(null));
    listBudgets().then((r) => setBudgets(r.budgets)).catch(() => setBudgets([]));
    listInvocations({ limit: 9, order: "recent" }).then((r) => setRecent(r.invocations)).catch(() => setRecent([]));
  }, []);

  const evalKpis = useMemo(() => {
    const passing = suites.filter((s) => s.passed).length;
    const regressions = suites.filter((s) => !s.passed).length;
    const drifting = suites.filter((s) => s.drift_status === "drifting").length;
    return { passing, regressions, drifting };
  }, [suites]);

  // weakest suites first — failing, then drifting, then by score
  const health = useMemo(
    () =>
      [...suites]
        .sort(
          (a, b) =>
            Number(a.passed) - Number(b.passed) ||
            Number(a.drift_status !== "drifting") - Number(b.drift_status !== "drifting") ||
            (a.latest_score || 0) - (b.latest_score || 0)
        )
        .slice(0, 5),
    [suites]
  );

  const byModule = useMemo(() => {
    if (!cost) return [];
    const m = new Map<string, { module: string; cost: number; calls: number }>();
    for (const b of cost.by_name) {
      const mod = b.name.includes(".") ? b.name.split(".")[0] : "other";
      const e = m.get(mod) || { module: mod, cost: 0, calls: 0 };
      e.cost += b.cost;
      e.calls += b.calls;
      m.set(mod, e);
    }
    return [...m.values()].sort((a, b) => b.cost - a.cost).slice(0, 7);
  }, [cost]);
  const moduleMax = Math.max(1e-9, ...byModule.map((r) => r.cost));

  // week-over-week spend delta from the daily series
  const wow = useMemo(() => {
    const d = cost?.by_date;
    if (!d || d.length < 14) return null;
    const tail = (n: number, o: number) => d.slice(d.length - o - n, d.length - o).reduce((a, r) => a + r.cost, 0);
    const last = tail(7, 0), prev = tail(7, 7);
    if (prev <= 0) return null;
    return (last - prev) / prev;
  }, [cost]);

  const cs = cost?.summary;
  const breachedCount = budgets.filter((b) => b.breached).length;

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · overview"
        title="Overview"
        description="Eval health and spend at a glance. Dive into Evals or Cost for detail."
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        <KPI
          label="Suites passing"
          value={`${evalKpis.passing}/${suites.length}`}
          accent="var(--success)"
          sub={evalKpis.regressions > 0 ? `${evalKpis.regressions} regression${evalKpis.regressions > 1 ? "s" : ""}` : "all green"}
        />
        <KPI label="Drifting" value={evalKpis.drifting} accent={evalKpis.drifting ? "var(--warning)" : "var(--text)"} sub={evalKpis.drifting ? "negative slope" : "all stable"} />
        <KPI
          label="Spend · 30d"
          value={cs ? usd(cs.total_cost, 2) : "—"}
          accent="var(--text)"
          sub={
            cs
              ? `${cs.total_calls.toLocaleString()} calls${wow != null ? `  ·  ${wow >= 0 ? "▲" : "▼"}${Math.abs(wow * 100).toFixed(0)}% wk/wk` : ""}`
              : ""
          }
        />
        <KPI
          label="Avg $/call"
          value={cs ? "$" + (cs.avg_cost ?? 0).toFixed(5) : "—"}
          accent="var(--accent)"
          sub={cs ? `${formatTokens(cs.total_tokens_in)} in · ${formatTokens(cs.total_tokens_out)} out` : ""}
        />
      </div>

      {/* health + budget governance */}
      <div style={{ display: "grid", gridTemplateColumns: "1.25fr 1fr", gap: 16, marginBottom: 16 }}>
        <SectionCard title="Suite health" sub="weakest first" action={<button className="btn" onClick={() => navigate("/evals")}>All evals ›</button>}>
          {health.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No eval suites yet.</div>
          ) : (
            <table className="pr">
              <tbody>
                {health.map((s) => (
                  <tr key={s.name} onClick={() => navigate(`/suite/${encodeURIComponent(s.name)}`)}>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</span>
                        {!s.passed ? <StatusPill status="regression" /> : s.drift_status === "drifting" ? <StatusPill status="drifting" /> : null}
                      </div>
                    </td>
                    <td className="c"><Sparkline scores={s.sparkline_scores} width={90} height={24} /></td>
                    <td className="r mono" style={{ fontSize: 14, fontWeight: 600, color: scoreColor(s.latest_score) }}>{pct(s.latest_score, 0)}</td>
                    <td className="mono" style={{ color: "var(--secondary)", fontSize: 11 }}>{relTime(s.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </SectionCard>

        <SectionCard
          title="Budgets"
          sub={breachedCount > 0 ? `${breachedCount} over budget` : "all within cap"}
          action={<button className="btn" onClick={() => navigate("/cost")}>Manage ›</button>}
        >
          {budgets.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No budgets set.</div>
          ) : (
            <div style={{ padding: "13px 16px" }}>
              {budgets.map((b) => {
                const label = b.scope === "global" ? "All prompts" : b.scope === "module" ? `module: ${b.target}` : b.target;
                const color = b.breached ? "var(--error)" : b.pct >= 80 ? "var(--warning)" : "var(--accent)";
                return (
                  <div key={b.id} style={{ marginBottom: 13 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                      <span className="mono" style={{ fontSize: 12 }}>
                        {label}<span style={{ color: "var(--muted)" }}> · {b.period}</span>
                      </span>
                      <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: b.breached ? "var(--error)" : "var(--text)" }}>
                        ${b.spend.toFixed(0)} / ${b.limit_usd.toFixed(0)} ({b.pct.toFixed(0)}%)
                      </span>
                    </div>
                    <div style={{ height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                      <div style={{ width: Math.min(100, b.pct) + "%", height: "100%", background: color }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </SectionCard>
      </div>

      {/* spend by module + live activity */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr", gap: 16 }}>
        <SectionCard title="Spend by module · 30d" action={<button className="btn" onClick={() => navigate("/cost")}>Cost detail ›</button>}>
          {byModule.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No cost data yet.</div>
          ) : (
            <div style={{ padding: "12px 16px" }}>
              {byModule.map((r) => (
                <div key={r.module} style={{ marginBottom: 11 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ fontSize: 12.5, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                      {r.module}<span style={{ color: "var(--muted)", marginLeft: 6, fontSize: 11 }}>{r.calls.toLocaleString()} calls</span>
                    </span>
                    <span className="mono" style={{ fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>${r.cost.toFixed(2)}</span>
                  </div>
                  <div style={{ height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                    <div style={{ width: (r.cost / moduleMax) * 100 + "%", height: "100%", background: "var(--accent)", opacity: 0.85 }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard title="Recent activity" sub="latest production calls">
          {recent.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No traffic captured yet.</div>
          ) : (
            <table className="pr">
              <tbody>
                {recent.map((r) => (
                  <tr key={r.id} onClick={() => navigate(`/invocations/${r.id}`)}>
                    <td className="mono" style={{ color: "var(--secondary)", fontSize: 11, whiteSpace: "nowrap" }}>{relTime(r.created_at)}</td>
                    <td><span className="mono" style={{ ...fade, fontSize: 12.5 }}>{r.prompt_name}</span></td>
                    <td>
                      {r.model && (
                        <span className="mono" style={{ fontSize: 10.5, color: "var(--secondary)", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 4, padding: "1px 6px" }}>
                          {r.model}
                        </span>
                      )}
                    </td>
                    <td className="r mono" style={{ fontSize: 12, color: "var(--accent)", whiteSpace: "nowrap" }}>{r.cost != null ? usd(r.cost, 4) : "—"}</td>
                    <td className="r mono" style={{ fontSize: 11, color: "var(--secondary)", whiteSpace: "nowrap" }}>{r.latency_ms != null ? `${r.latency_ms}ms` : "—"}</td>
                    <td className="c"><span title={r.rating != null ? `rating ${r.rating}` : "no feedback"} style={{ display: "inline-block", width: 7, height: 7, borderRadius: "50%", background: ratingColor(r.rating) }} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
