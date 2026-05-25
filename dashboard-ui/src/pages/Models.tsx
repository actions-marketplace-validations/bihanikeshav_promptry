import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { PageHeader, StatusPill, Select } from "../components/ui";
import { pct, scoreColor } from "../utils";
import { getModelVersions, compareModels } from "../api/client";
import type { ModelCompareReport, ModelVersion } from "../api/types";
import type { LayoutContext } from "../components/Layout";

export default function Models() {
  const { suites } = useOutletContext<LayoutContext>();
  const [suiteName, setSuiteName] = useState<string>("");
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [baseline, setBaseline] = useState<string>("");
  const [candidate, setCandidate] = useState<string>("");
  const [report, setReport] = useState<ModelCompareReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!suiteName && suites.length > 0) setSuiteName(suites[0].name);
  }, [suiteName, suites]);

  useEffect(() => {
    if (!suiteName) return;
    getModelVersions(suiteName)
      .then((r) => {
        setVersions(r.versions);
        if (r.versions[0]) setBaseline(r.versions[0].model_version);
        if (r.versions[1]) setCandidate(r.versions[1].model_version);
      })
      .catch(() => setVersions([]));
  }, [suiteName]);

  const runCompare = async () => {
    if (!suiteName || !baseline || !candidate) return;
    setLoading(true);
    setError(null);
    try {
      const r = await compareModels(suiteName, baseline, candidate);
      setReport(r);
    } catch (e) {
      setError(String(e));
      setReport(null);
    } finally {
      setLoading(false);
    }
  };

  const verdictStyle =
    report?.verdict === "switch"
      ? { label: "SWITCH TO CANDIDATE", color: "var(--success)", bg: "var(--success-soft)" }
      : report?.verdict === "keep_baseline"
        ? { label: "KEEP BASELINE", color: "var(--error)", bg: "var(--error-soft)" }
        : { label: "COMPARABLE", color: "var(--warning)", bg: "var(--warning-soft)" };

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · models"
        title="Model Comparison"
        description="Pit two models against each other on the same suite. Statistical tests and cost-per-score included."
      />

      <div
        className="card"
        style={{
          padding: 16,
          marginBottom: 20,
          display: "grid",
          gridTemplateColumns: "1.3fr 1fr 1fr auto",
          gap: 12,
          alignItems: "end",
        }}
      >
        <div>
          <div
            style={{
              fontSize: 10,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: 4,
              fontFamily: "var(--font-mono)",
            }}
          >
            Suite
          </div>
          <Select
            value={suiteName}
            onChange={setSuiteName}
            minWidth={0}
            options={suites.map((s) => ({ value: s.name, label: s.name }))}
          />
        </div>
        <div>
          <div
            style={{
              fontSize: 10,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: 4,
              fontFamily: "var(--font-mono)",
            }}
          >
            Baseline
          </div>
          <Select
            value={baseline}
            onChange={setBaseline}
            minWidth={0}
            options={
              versions.length === 0
                ? [{ value: "", label: "— no models —" }]
                : versions.map((v) => ({
                    value: v.model_version,
                    label: `${v.model_version} (${v.run_count})`,
                  }))
            }
          />
        </div>
        <div>
          <div
            style={{
              fontSize: 10,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: 4,
              fontFamily: "var(--font-mono)",
            }}
          >
            Candidate
          </div>
          <Select
            value={candidate}
            onChange={setCandidate}
            minWidth={0}
            options={
              versions.length === 0
                ? [{ value: "", label: "— no models —" }]
                : versions.map((v) => ({
                    value: v.model_version,
                    label: `${v.model_version} (${v.run_count})`,
                  }))
            }
          />
        </div>
        <button
          className="btn btn-primary"
          style={{ alignSelf: "stretch" }}
          onClick={runCompare}
          disabled={loading || !baseline || !candidate || baseline === candidate}
        >
          {loading ? "Running…" : "Run comparison"}
        </button>
      </div>

      {versions.length < 2 && suiteName && (
        <div
          className="card"
          style={{
            padding: 14,
            marginBottom: 20,
            color: "var(--secondary)",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <span
            className="mono"
            style={{
              fontSize: 10,
              color: "var(--warning)",
              background: "var(--warning-soft)",
              padding: "2px 8px",
              borderRadius: 4,
              border: "1px solid var(--warning)22",
              fontWeight: 600,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
            }}
          >
            No comparison
          </span>
          <span>
            {versions.length === 0
              ? `Suite "${suiteName}" has no recorded model runs yet.`
              : `Only one model (${versions[0]?.model_version}) has been run on this suite — need at least two to compare.`}
          </span>
        </div>
      )}

      {error && (
        <div
          className="card"
          style={{ padding: 14, marginBottom: 20, color: "var(--error)", fontSize: 13 }}
        >
          {error}
        </div>
      )}

      {report && (
        <>
          <div
            className="card-elev noise"
            style={{
              padding: 20,
              marginBottom: 20,
              display: "grid",
              gridTemplateColumns: "auto 1fr auto",
              gap: 20,
              alignItems: "center",
            }}
          >
            <div
              style={{
                padding: "10px 16px",
                background: verdictStyle.bg,
                color: verdictStyle.color,
                borderRadius: 8,
                fontWeight: 700,
                letterSpacing: "0.04em",
                fontSize: 14,
              }}
            >
              {verdictStyle.label}
            </div>
            <div style={{ fontSize: 13, color: "var(--text-dim)", lineHeight: 1.55 }}>
              {report.verdict_reason}
            </div>
            <div style={{ textAlign: "right" }}>
              <div
                className="mono"
                style={{
                  fontSize: 28,
                  fontWeight: 600,
                  color: report.overall_delta >= 0 ? "var(--success)" : "var(--error)",
                }}
              >
                {report.overall_delta >= 0 ? "+" : ""}
                {(report.overall_delta * 100).toFixed(1)}pp
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                p{report.percentile} confidence
              </div>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
            {[report.baseline, report.candidate].map((m, i) => (
              <div
                key={i}
                className="card"
                style={{
                  padding: 16,
                  borderColor: i === 1 ? "var(--accent-line)" : "var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    marginBottom: 8,
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--muted)",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      fontFamily: "var(--font-mono)",
                    }}
                  >
                    {i === 0 ? "baseline" : "candidate"}
                  </div>
                  <span className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
                    {m.run_count} runs
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 18,
                    fontWeight: 600,
                    color: "var(--text)",
                    marginBottom: 8,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {m.model_version}
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: 36,
                    fontWeight: 600,
                    color: scoreColor(m.overall_mean),
                    lineHeight: 1,
                  }}
                >
                  {pct(m.overall_mean, 1)}
                </div>
                <div
                  style={{
                    marginTop: 8,
                    fontSize: 11,
                    color: "var(--muted)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  σ {m.overall_std.toFixed(3)}
                  {i === 0 && report.score_per_dollar_baseline != null
                    ? ` · ${report.score_per_dollar_baseline.toFixed(3)} score/$`
                    : ""}
                  {i === 1 && report.score_per_dollar_candidate != null
                    ? ` · ${report.score_per_dollar_candidate.toFixed(3)} score/$`
                    : ""}
                </div>
              </div>
            ))}
          </div>

          <div className="card" style={{ overflow: "hidden" }}>
            <div
              style={{
                padding: "14px 16px",
                borderBottom: "1px solid var(--border)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              Per-assertion comparison
            </div>
            <table className="pr">
              <thead>
                <tr>
                  <th>Assertion</th>
                  <th className="r">Baseline</th>
                  <th className="r">Candidate</th>
                  <th className="r">Δ</th>
                  <th>Delta (visual)</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {report.assertion_comparisons.map((ac) => {
                  const dir = ac.delta > 0 ? 1 : ac.delta < 0 ? -1 : 0;
                  const w = Math.min(100, Math.abs(ac.delta * 400));
                  return (
                    <tr key={ac.assertion_type}>
                      <td>{ac.assertion_type}</td>
                      <td className="r mono" style={{ color: "var(--text-dim)" }}>
                        {pct(ac.baseline_mean, 1)}
                      </td>
                      <td className="r mono" style={{ color: "var(--text-dim)" }}>
                        {pct(ac.candidate_score, 1)}
                      </td>
                      <td
                        className="r mono"
                        style={{
                          color:
                            dir > 0 ? "var(--success)" : dir < 0 ? "var(--error)" : "var(--muted)",
                          fontWeight: 600,
                        }}
                      >
                        {dir > 0 ? "+" : ""}
                        {(ac.delta * 100).toFixed(1)}pp
                      </td>
                      <td>
                        <div
                          style={{
                            position: "relative",
                            height: 10,
                            background: "var(--bg-elev)",
                            borderRadius: 2,
                            border: "1px solid var(--border)",
                            minWidth: 180,
                          }}
                        >
                          <div
                            style={{
                              position: "absolute",
                              left: "50%",
                              width: 1,
                              top: -2,
                              bottom: -2,
                              background: "var(--border-strong)",
                            }}
                          />
                          {dir !== 0 && (
                            <div
                              style={{
                                position: "absolute",
                                top: 0,
                                bottom: 0,
                                left: dir > 0 ? "50%" : `calc(50% - ${w / 2}%)`,
                                width: `${w / 2}%`,
                                background: dir > 0 ? "var(--success)" : "var(--error)",
                                opacity: 0.8,
                              }}
                            />
                          )}
                        </div>
                      </td>
                      <td>
                        <StatusPill
                          status={
                            ac.verdict === "better"
                              ? "improved"
                              : ac.verdict === "worse"
                                ? "regressed"
                                : "unchanged"
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!report && !loading && !error && versions.length >= 2 && (
        <div className="card" style={{ padding: 30, textAlign: "center", color: "var(--muted)" }}>
          Select baseline and candidate models, then run comparison.
        </div>
      )}
    </div>
  );
}
