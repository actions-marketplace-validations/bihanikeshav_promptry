import { useEffect, useMemo, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { KPI, PageHeader, Sparkline, StatusPill } from "../components/ui";
import { pct, relTime, usd, formatTokens, scoreColor } from "../utils";
import { getCostData } from "../api/client";
import type { LayoutContext } from "../components/Layout";
import type { CostResponse } from "../api/types";

export default function Overview() {
  const { suites } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();
  const [cost, setCost] = useState<CostResponse | null>(null);

  useEffect(() => {
    getCostData(30).then(setCost).catch(() => setCost(null));
  }, []);

  const evalKpis = useMemo(() => {
    const passing = suites.filter((s) => s.passed).length;
    const regressions = suites.filter((s) => !s.passed).length;
    const drifting = suites.filter((s) => s.drift_status === "drifting").length;
    return { passing, regressions, drifting };
  }, [suites]);

  // Worst-performing suites that need eyes.
  const attention = useMemo(
    () =>
      suites
        .filter((s) => !s.passed || s.drift_status === "drifting")
        .sort((a, b) => (a.latest_score || 0) - (b.latest_score || 0))
        .slice(0, 5),
    [suites]
  );

  // Cost rolled up per module (name prefix).
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
    return [...m.values()].sort((a, b) => b.cost - a.cost).slice(0, 6);
  }, [cost]);

  const moduleMax = Math.max(1e-9, ...byModule.map((r) => r.cost));
  const cs = cost?.summary;

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · overview"
        title="Overview"
        description="Eval health and spend at a glance. Dive into Evals or Cost for detail."
      />

      {/* Blended KPI row: eval health + spend */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 22 }}>
        <KPI
          label="Suites passing"
          value={`${evalKpis.passing}/${suites.length}`}
          accent="var(--success)"
          sub={evalKpis.regressions > 0 ? `${evalKpis.regressions} regressions` : "all green"}
        />
        <KPI label="Drifting" value={evalKpis.drifting} accent="var(--warning)" sub="negative slope" />
        <KPI
          label="Spend (30d)"
          value={cs ? usd(cs.total_cost, 2) : "—"}
          accent="var(--text)"
          sub={cs ? `${cs.total_calls.toLocaleString()} calls` : ""}
        />
        <KPI
          label="Avg $/call"
          value={cs ? "$" + (cs.avg_cost ?? 0).toFixed(5) : "—"}
          accent="var(--accent)"
          sub={cs ? `${formatTokens(cs.total_tokens_in)} in · ${formatTokens(cs.total_tokens_out)} out` : ""}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* Needs attention (evals) */}
        <div className="card" style={{ overflow: "hidden" }}>
          <div
            style={{
              padding: "13px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>Needs attention</div>
            <button className="btn" onClick={() => navigate("/evals")}>
              All evals ›
            </button>
          </div>
          {attention.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
              All suites passing and stable. 🌿
            </div>
          ) : (
            <table className="pr">
              <tbody>
                {attention.map((s) => (
                  <tr key={s.name} onClick={() => navigate(`/suite/${encodeURIComponent(s.name)}`)}>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{s.name}</span>
                        {!s.passed && <StatusPill status="regression" />}
                      </div>
                    </td>
                    <td className="c">
                      <Sparkline scores={s.sparkline_scores} width={90} height={24} />
                    </td>
                    <td className="r mono" style={{ fontSize: 14, fontWeight: 600, color: scoreColor(s.latest_score) }}>
                      {pct(s.latest_score, 0)}
                    </td>
                    <td className="mono" style={{ color: "var(--secondary)", fontSize: 11 }}>
                      {relTime(s.timestamp)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Spend by module */}
        <div className="card" style={{ overflow: "hidden" }}>
          <div
            style={{
              padding: "13px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>Spend by module · 30d</div>
            <button className="btn" onClick={() => navigate("/cost")}>
              Cost detail ›
            </button>
          </div>
          {byModule.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
              No cost data yet.
            </div>
          ) : (
            <div style={{ padding: "10px 16px" }}>
              {byModule.map((r) => (
                <div key={r.module} style={{ marginBottom: 11 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ fontSize: 12.5, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                      {r.module}
                      <span style={{ color: "var(--muted)", marginLeft: 6, fontSize: 11 }}>
                        {r.calls.toLocaleString()} calls
                      </span>
                    </span>
                    <span className="mono" style={{ fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>
                      ${r.cost.toFixed(2)}
                    </span>
                  </div>
                  <div style={{ height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                    <div style={{ width: (r.cost / moduleMax) * 100 + "%", height: "100%", background: "var(--accent)", opacity: 0.85 }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
