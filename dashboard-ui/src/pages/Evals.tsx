import { useMemo, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { KPI, PageHeader, Sparkline, StatusPill, Select } from "../components/ui";
import { pct, relTime, scoreColor } from "../utils";
import type { LayoutContext } from "../components/Layout";
import type { SuiteSummary } from "../api/types";

type Filter = "all" | "regressions" | "drifting";
type Sort = "status" | "score" | "time";

function prevScore(s: SuiteSummary): number | null {
  const arr = s.sparkline_scores;
  if (!arr || arr.length < 2) return null;
  return arr[arr.length - 2];
}

export default function Evals() {
  const { suites, refresh } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("status");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    let arr = suites.slice();
    if (filter === "regressions") arr = arr.filter((s) => !s.passed);
    if (filter === "drifting") arr = arr.filter((s) => s.drift_status === "drifting");
    const qq = query.trim().toLowerCase();
    if (qq) arr = arr.filter((s) => s.name.toLowerCase().includes(qq));
    arr.sort((a, b) => {
      if (sort === "status") {
        const aw = !a.passed ? 0 : a.drift_status === "drifting" ? 1 : 2;
        const bw = !b.passed ? 0 : b.drift_status === "drifting" ? 1 : 2;
        if (aw !== bw) return aw - bw;
      }
      if (sort === "score") return (a.latest_score || 0) - (b.latest_score || 0);
      return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
    });
    return arr;
  }, [suites, filter, sort, query]);

  const kpis = useMemo(() => {
    const passing = suites.filter((s) => s.passed).length;
    const regressions = suites.filter((s) => !s.passed).length;
    const drifting = suites.filter((s) => s.drift_status === "drifting").length;
    return { passing, regressions, drifting };
  }, [suites]);

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · evals"
        title="Eval Suites"
        description="Continuous evaluation of every prompt. Regressions surface the moment a run falls below threshold."
        actions={
          <>
          <button className="btn btn-primary" onClick={() => navigate("/suites/new")}>New suite</button>
          <button className="btn" onClick={refresh}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2 6a4 4 0 0 1 7-2.6M10 6a4 4 0 0 1-7 2.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              <path d="M9 1v2.6H6.6M3 11V8.4h2.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Refresh
          </button>
          </>
        }
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <KPI
          label="Passing"
          value={`${kpis.passing}/${suites.length}`}
          accent="var(--success)"
          sub="above 75% threshold"
        />
        <KPI label="Regressions" value={kpis.regressions} accent="var(--error)" sub="need attention" />
        <KPI label="Drifting" value={kpis.drifting} accent="var(--warning)" sub="negative slope" />
        <KPI
          label="Total suites"
          value={suites.length}
          accent="var(--text)"
          sub={
            suites.length > 0
              ? `last sync ${relTime(
                  suites.reduce((a, b) =>
                    new Date(a.timestamp) > new Date(b.timestamp) ? a : b
                  ).timestamp
                )}`
              : "no data yet"
          }
        />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <div
          style={{
            display: "inline-flex",
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: 2,
          }}
        >
          {[
            { k: "all" as const, label: "All suites", n: suites.length },
            { k: "regressions" as const, label: "Regressions", n: kpis.regressions },
            { k: "drifting" as const, label: "Drifting", n: kpis.drifting },
          ].map((opt) => (
            <button
              key={opt.k}
              onClick={() => setFilter(opt.k)}
              style={{
                padding: "5px 11px",
                fontSize: 12,
                borderRadius: 4,
                background: filter === opt.k ? "var(--bg-elev)" : "transparent",
                border: filter === opt.k ? "1px solid var(--border)" : "1px solid transparent",
                color: filter === opt.k ? "var(--text)" : "var(--secondary)",
                cursor: "pointer",
                fontWeight: filter === opt.k ? 600 : 500,
                fontFamily: "var(--font-ui)",
              }}
            >
              {opt.label}{" "}
              <span className="mono" style={{ marginLeft: 5, color: "var(--muted)", fontSize: 11 }}>
                {opt.n}
              </span>
            </button>
          ))}
        </div>
        <div style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 8 }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search suites…"
            style={{ background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 6, padding: "5px 10px", color: "var(--text)", fontSize: 12, outline: "none", width: 170 }}
          />
          <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Sort
          </span>
          <Select
            value={sort}
            onChange={(v) => setSort(v as Sort)}
            minWidth={200}
            options={[
              { value: "status", label: "Status · regressions first" },
              { value: "score", label: "Score · low to high" },
              { value: "time", label: "Last run" },
            ]}
          />
        </div>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ maxHeight: 600, overflowY: "auto" }}>
        <table className="pr">
          <thead>
            <tr>
              <th style={{ width: 28 }}></th>
              <th>Suite</th>
              <th>Model</th>
              <th>Prompt</th>
              <th className="r">Score</th>
              <th>Δ 7d</th>
              <th className="c">Trend</th>
              <th>Drift</th>
              <th>Last run</th>
              <th className="r"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const prev = prevScore(s);
              const delta = prev != null && s.latest_score != null ? s.latest_score - prev : null;
              const rowCls = !s.passed ? "regressed" : s.drift_status === "drifting" ? "drifting" : "";
              return (
                <tr
                  key={s.name}
                  className={rowCls}
                  onClick={() => navigate(`/suite/${encodeURIComponent(s.name)}`)}
                >
                  <td style={{ textAlign: "center" }}>
                    <span
                      style={{
                        display: "inline-block",
                        width: 8,
                        height: 8,
                        borderRadius: 999,
                        background: !s.passed
                          ? "var(--error)"
                          : s.drift_status === "drifting"
                            ? "var(--warning)"
                            : "var(--success)",
                        boxShadow: `0 0 0 3px ${
                          !s.passed
                            ? "rgba(248,113,113,0.15)"
                            : s.drift_status === "drifting"
                              ? "rgba(251,191,36,0.15)"
                              : "rgba(74,222,128,0.12)"
                        }`,
                      }}
                    />
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>{s.name}</span>
                      {!s.passed && <StatusPill status="regression" />}
                    </div>
                  </td>
                  <td className="mono" style={{ color: "var(--text-dim)", fontSize: 12 }}>
                    {s.model_version ?? "—"}
                  </td>
                  <td className="mono" style={{ color: "var(--text-dim)", fontSize: 12 }}>
                    {s.prompt_version != null ? `v${s.prompt_version}` : "—"}
                  </td>
                  <td
                    className="r mono"
                    style={{ fontSize: 15, fontWeight: 600, color: scoreColor(s.latest_score) }}
                  >
                    {pct(s.latest_score, 0)}
                  </td>
                  <td
                    className="mono"
                    style={{
                      color: delta == null ? "var(--muted)" : delta >= 0 ? "var(--success)" : "var(--error)",
                      fontSize: 12,
                    }}
                  >
                    {delta == null ? "—" : (delta >= 0 ? "+" : "") + (delta * 100).toFixed(1)}
                  </td>
                  <td className="c">
                    <Sparkline scores={s.sparkline_scores} width={110} height={28} />
                  </td>
                  <td>
                    <StatusPill status={s.drift_status} />
                  </td>
                  <td className="mono" style={{ color: "var(--secondary)", fontSize: 11.5 }}>
                    {relTime(s.timestamp)}
                  </td>
                  <td className="r">
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
                      <button
                        className="btn btn-ghost"
                        onClick={(e) => { e.stopPropagation(); navigate(`/suites/new?edit=${encodeURIComponent(s.name)}`); }}
                        title="Edit this suite"
                        style={{ fontSize: 11, padding: "2px 8px", color: "var(--accent)" }}
                      >
                        Edit
                      </button>
                      <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 14 }}>›</span>
                    </div>
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={10} style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
                  {suites.length === 0
                    ? "No eval suites yet — create one with the New suite button, then run it."
                    : "No suites match this filter."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  );
}
