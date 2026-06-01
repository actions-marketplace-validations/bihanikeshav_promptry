import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { KPI, PageHeader, Sparkline, StatusPill } from "../components/ui";
import { pct, relTime, usd, scoreColor } from "../utils";
import { getCostData, listBudgets, listFeedback, getFeedbackStats } from "../api/client";
import type { LayoutContext } from "../components/Layout";
import type { CostResponse, BudgetStatus, FeedbackRow, FeedbackStats } from "../api/types";

/* one-line clamp that fades instead of cutting with an ellipsis */
const fade: React.CSSProperties = {
  display: "block", maxWidth: 150, overflow: "hidden", whiteSpace: "nowrap",
  maskImage: "linear-gradient(90deg,#000 78%,transparent)",
  WebkitMaskImage: "linear-gradient(90deg,#000 78%,transparent)",
};

function SectionCard({ title, sub, action, children, style }: { title: string; sub?: string; action?: ReactNode; children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column", ...style }}>
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
  const [fbStats, setFbStats] = useState<FeedbackStats | null>(null);
  const [recentFb, setRecentFb] = useState<FeedbackRow[]>([]);

  useEffect(() => {
    getCostData(30).then(setCost).catch(() => setCost(null));
    listBudgets().then((r) => setBudgets(r.budgets)).catch(() => setBudgets([]));
    getFeedbackStats(30).then(setFbStats).catch(() => setFbStats(null));
    listFeedback({ days: 30, limit: 5 }).then((r) => setRecentFb(r.feedback)).catch(() => setRecentFb([]));
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

      {/* eval-health hero + cost KPIs + budget governance */}
      <div style={{ display: "grid", gridTemplateColumns: "1.35fr 1fr", gap: 16, marginBottom: 16, alignItems: "stretch" }}>
        {/* Suite health hero: summary stat band + weakest-first list */}
        <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "13px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Suite health</div>
            <button className="btn" onClick={() => navigate("/evals")}>All evals ›</button>
          </div>
          <div style={{ display: "flex", borderBottom: "1px solid var(--border)" }}>
            {[
              { v: `${evalKpis.passing}/${suites.length}`, l: "passing", c: "var(--success)" },
              { v: evalKpis.drifting, l: "drifting", c: evalKpis.drifting ? "var(--warning)" : "var(--muted)" },
              { v: evalKpis.regressions, l: evalKpis.regressions === 1 ? "regression" : "regressions", c: evalKpis.regressions ? "var(--error)" : "var(--muted)" },
            ].map((st, i) => (
              <div key={i} style={{ flex: 1, padding: "13px 16px", borderRight: i < 2 ? "1px solid var(--border)" : "none" }}>
                <div className="mono" style={{ fontSize: 23, fontWeight: 600, color: st.c, lineHeight: 1 }}>{st.v}</div>
                <div style={{ fontSize: 11, color: "var(--secondary)", textTransform: "uppercase", letterSpacing: "0.06em", marginTop: 6 }}>{st.l}</div>
              </div>
            ))}
          </div>
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
        </div>

        {/* right column: cost KPIs stacked over budgets */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <KPI
              label="Spend · 30d"
              value={cs ? usd(cs.total_cost, 2) : "—"}
              accent="var(--text)"
              sub={cs ? `${cs.total_calls.toLocaleString()} calls${wow != null ? `  ·  ${wow >= 0 ? "▲" : "▼"}${Math.abs(wow * 100).toFixed(0)}% wk/wk` : ""}` : ""}
            />
            <KPI
              label="Satisfaction"
              value={fbStats?.positive_rate != null ? pct(fbStats.positive_rate, 0) : "—"}
              accent="var(--success)"
              sub={fbStats ? `${fbStats.rated.toLocaleString()} ratings${fbStats.negative ? `  ·  ${fbStats.negative} flagged` : ""}` : ""}
              spark={fbStats?.sparkline?.length ? fbStats.sparkline : undefined}
            />
          </div>
          <SectionCard
            title="Budgets"
            sub={breachedCount > 0 ? `${breachedCount} over budget` : "all within cap"}
            action={<button className="btn" onClick={() => navigate("/cost")}>Manage ›</button>}
            style={{ flex: 1 }}
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

        <SectionCard title="Recent feedback" sub="what end-users are saying" action={<button className="btn" onClick={() => navigate("/feedback")}>All feedback ›</button>}>
          {recentFb.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No feedback captured yet.</div>
          ) : (
            <table className="pr" style={{ tableLayout: "fixed", width: "100%" }}>
              <tbody>
                {recentFb.map((r) => (
                  <tr key={r.id} onClick={() => r.invocation_id && navigate(`/invocations/${r.invocation_id}`, { state: { from: [{ label: "feedback", to: "/feedback" }] } })} style={{ cursor: r.invocation_id ? "pointer" : "default" }}>
                    <td style={{ width: 26, paddingRight: 0 }}>
                      <span title={r.rating != null ? `rating ${r.rating}` : "no rating"} style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: ratingColor(r.rating) }} />
                    </td>
                    <td style={{ width: "30%" }}><span className="mono" style={{ ...fade, fontSize: 12 }}>{r.prompt_name}</span></td>
                    <td style={{ fontSize: 12.5, color: r.comment ? "var(--text)" : "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.comment?.trim() || <span style={{ fontStyle: "italic" }}>rated, no note</span>}
                    </td>
                    <td className="r mono" style={{ width: 64, paddingRight: 16, fontSize: 11, color: "var(--secondary)", whiteSpace: "nowrap" }}>{relTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {recentFb.length > 0 && fbStats && fbStats.total > 5 && (
            <div
              onClick={() => navigate("/feedback")}
              style={{ padding: "9px 16px", borderTop: "1px solid var(--border)", cursor: "pointer", fontSize: 11.5, color: "var(--secondary)" }}
            >
              … {(fbStats.total - 5).toLocaleString()} more
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
