import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Breadcrumbs, KPI, PageHeader, ScoreChart, StatusPill } from "../components/ui";
import type { Marker, ScorePoint } from "../components/ui";
import { pct, relTime, scoreColor, formatTime } from "../utils";
import { getSuiteRuns } from "../api/client";
import type { EvalRun } from "../api/types";

export default function SuiteDetail() {
  const { name = "" } = useParams();
  const navigate = useNavigate();
  const suiteName = decodeURIComponent(name);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getSuiteRuns(suiteName, 50)
      .then((r) => setRuns(r))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, [suiteName]);

  const latest = runs[0];
  const previous = runs[1];

  const { chartData, markers } = useMemo(() => {
    const data: ScorePoint[] = runs
      .slice()
      .reverse()
      .map((r) => ({
        label: `#${r.id} · ${new Date(r.timestamp).toLocaleDateString([], { month: "short", day: "numeric" })}`,
        score: r.overall_score ?? 0,
        promptVersion: r.prompt_version,
        modelVersion: r.model_version,
      }));
    const marks: Marker[] = [];
    for (let i = 1; i < data.length; i++) {
      if (data[i].promptVersion !== data[i - 1].promptVersion) {
        marks.push({ index: i, label: `v${data[i].promptVersion ?? "?"}` });
      }
    }
    return { chartData: data, markers: marks };
  }, [runs]);

  if (loading && runs.length === 0) {
    return (
      <div>
        <Breadcrumbs items={[{ label: "overview", to: "/" }, { label: suiteName }]} />
        <PageHeader eyebrow="Suite" title={suiteName} description="Loading runs…" />
      </div>
    );
  }

  if (!latest) {
    return (
      <div>
        <Breadcrumbs items={[{ label: "overview", to: "/" }, { label: suiteName }]} />
        <PageHeader eyebrow="Suite" title={suiteName} description="No runs found for this suite." />
      </div>
    );
  }

  const canDiff = !!previous;

  return (
    <div>
      <Breadcrumbs items={[{ label: "overview", to: "/" }, { label: suiteName }]} />

      <PageHeader
        eyebrow="Suite"
        title={suiteName}
        tags={!latest.overall_pass ? ["REGRESSION"] : []}
        description={`${latest.model_version ?? "—"} · prompt v${latest.prompt_version ?? "—"}. ${runs.length} runs, latest ${relTime(latest.timestamp)}.`}
        actions={
          canDiff && (
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/suite/${encodeURIComponent(suiteName)}/diff`)}
            >
              <svg width="12" height="12" viewBox="0 0 12 12">
                <path d="M3 2v8M9 2v8M2 4h2M2 8h2M8 4h2M8 8h2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              </svg>
              Compare last 2
            </button>
          )
        }
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <KPI
          label="Status"
          value={latest.overall_pass ? "PASS" : "FAIL"}
          accent={latest.overall_pass ? "var(--success)" : "var(--error)"}
          sub={`run #${latest.id}`}
        />
        <KPI
          label="Overall score"
          value={pct(latest.overall_score, 1)}
          accent={scoreColor(latest.overall_score)}
          sub="latest run"
        />
        <KPI
          label="Runs tracked"
          value={runs.length}
          accent="var(--text)"
          sub={`since ${new Date(runs[runs.length - 1].timestamp).toLocaleDateString()}`}
        />
        <KPI
          label="Δ vs previous"
          value={
            previous && previous.overall_score != null && latest.overall_score != null
              ? `${latest.overall_score - previous.overall_score >= 0 ? "+" : ""}${((latest.overall_score - previous.overall_score) * 100).toFixed(1)}pp`
              : "—"
          }
          accent={
            previous && previous.overall_score != null && latest.overall_score != null
              ? latest.overall_score - previous.overall_score >= 0
                ? "var(--success)"
                : "var(--error)"
              : "var(--text-dim)"
          }
          sub={previous ? `vs #${previous.id}` : "no previous run"}
        />
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Score history</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              {runs.length} runs · prompt version bumps shown as orange ticks
            </div>
          </div>
        </div>
        <ScoreChart data={chartData} markers={markers} height={220} />
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <div
          style={{
            padding: "14px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600 }}>Recent runs</div>
          <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
            showing {Math.min(20, runs.length)} of {runs.length}
          </div>
        </div>
        <table className="pr">
          <thead>
            <tr>
              <th style={{ width: 72 }}>Run</th>
              <th style={{ width: 80 }}>Status</th>
              <th className="r" style={{ width: 82 }}>
                Score
              </th>
              <th style={{ width: 90 }}>Δ</th>
              <th>Model</th>
              <th>Prompt</th>
              <th>Time</th>
              <th className="r"></th>
            </tr>
          </thead>
          <tbody>
            {runs.slice(0, 20).map((r, i) => {
              const prev = runs[i + 1];
              const delta =
                prev && prev.overall_score != null && r.overall_score != null
                  ? r.overall_score - prev.overall_score
                  : null;
              return (
                <tr
                  key={r.id}
                  onClick={() => navigate(`/suite/${encodeURIComponent(suiteName)}/run/${r.id}`)}
                >
                  <td className="mono" style={{ color: "var(--text-dim)" }}>
                    #{r.id}
                  </td>
                  <td>
                    <StatusPill status={r.overall_pass ? "pass" : "fail"} />
                  </td>
                  <td
                    className="r mono"
                    style={{ color: scoreColor(r.overall_score), fontWeight: 600, fontSize: 13 }}
                  >
                    {pct(r.overall_score, 1)}
                  </td>
                  <td
                    className="mono"
                    style={{
                      color: delta == null ? "var(--muted)" : delta >= 0 ? "var(--success)" : "var(--error)",
                      fontSize: 11.5,
                    }}
                  >
                    {delta == null ? "—" : (delta >= 0 ? "+" : "") + (delta * 100).toFixed(1) + "pp"}
                  </td>
                  <td className="mono" style={{ color: "var(--text-dim)", fontSize: 12 }}>
                    {r.model_version ?? "—"}
                  </td>
                  <td className="mono" style={{ color: "var(--text-dim)", fontSize: 12 }}>
                    {r.prompt_version != null ? `v${r.prompt_version}` : "—"}
                  </td>
                  <td className="mono" style={{ color: "var(--muted)", fontSize: 11.5 }}>
                    {formatTime(r.timestamp)}
                  </td>
                  <td className="r">
                    <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 14 }}>›</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
