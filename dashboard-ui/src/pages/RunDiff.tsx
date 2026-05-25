import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Breadcrumbs, PageHeader, StatusPill } from "../components/ui";
import { pct, scoreColor } from "../utils";
import { getSuiteRuns, getRunDiff } from "../api/client";
import type { RunDiff as RunDiffData } from "../api/types";

export default function RunDiff() {
  const { name = "" } = useParams();
  const suiteName = decodeURIComponent(name);
  const [diff, setDiff] = useState<RunDiffData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const runs = await getSuiteRuns(suiteName, 2);
        if (runs.length < 2) {
          setError("Need at least 2 runs to compare.");
          return;
        }
        const [current, baseline] = runs;
        const d = await getRunDiff(current.id, baseline.id);
        setDiff(d);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [suiteName]);

  if (error) {
    return (
      <div>
        <Breadcrumbs
          items={[
            { label: "overview", to: "/" },
            { label: suiteName, to: `/suite/${encodeURIComponent(suiteName)}` },
            { label: "diff" },
          ]}
        />
        <PageHeader eyebrow="Run comparison" title="Unable to compare" description={error} />
      </div>
    );
  }
  if (!diff) {
    return (
      <div>
        <Breadcrumbs
          items={[
            { label: "overview", to: "/" },
            { label: suiteName, to: `/suite/${encodeURIComponent(suiteName)}` },
            { label: "diff" },
          ]}
        />
        <PageHeader eyebrow="Run comparison" title="Loading…" />
      </div>
    );
  }

  const delta = diff.score_delta ?? 0;
  const deltaColor = delta >= 0 ? "var(--success)" : "var(--error)";

  return (
    <div>
      <Breadcrumbs
        items={[
          { label: "overview", to: "/" },
          { label: suiteName, to: `/suite/${encodeURIComponent(suiteName)}` },
          { label: `diff #${diff.current.id} ↔ #${diff.baseline.id}` },
        ]}
      />

      <PageHeader
        eyebrow="Run comparison"
        title={`#${diff.current.id} vs #${diff.baseline.id}`}
        description="Per-test delta showing which assertions regressed."
      />

      <div
        className="card-elev noise"
        style={{
          padding: "18px 20px",
          marginBottom: 20,
          display: "grid",
          gridTemplateColumns: "1fr auto 1fr",
          gap: 24,
          alignItems: "center",
        }}
      >
        <div>
          <div
            style={{
              fontSize: 11,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              fontFamily: "var(--font-mono)",
            }}
          >
            Baseline · #{diff.baseline.id}
          </div>
          <div
            className="mono"
            style={{
              fontSize: 32,
              fontWeight: 600,
              color: scoreColor(diff.baseline.score),
              lineHeight: 1.1,
              marginTop: 4,
            }}
          >
            {pct(diff.baseline.score, 1)}
          </div>
          <div className="mono" style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 6 }}>
            {diff.baseline.model_version ?? "—"} · prompt v{diff.baseline.prompt_version ?? "—"}
          </div>
        </div>
        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontSize: 11,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              fontFamily: "var(--font-mono)",
            }}
          >
            Δ score
          </div>
          <div
            className="mono"
            style={{ fontSize: 40, fontWeight: 700, color: deltaColor, lineHeight: 1.05, marginTop: 2 }}
          >
            {delta >= 0 ? "+" : ""}
            {(delta * 100).toFixed(1)}pp
          </div>
          <div style={{ display: "flex", gap: 6, justifyContent: "center", marginTop: 10, flexWrap: "wrap" }}>
            <span
              className="pill"
              style={{ color: "var(--error)", background: "var(--error-soft)", border: "1px solid var(--error)22" }}
            >
              ● {diff.summary.regressed} regressed
            </span>
            <span
              className="pill"
              style={{ color: "var(--success)", background: "var(--success-soft)", border: "1px solid var(--success)22" }}
            >
              ● {diff.summary.improved} improved
            </span>
            <span className="pill pill-outline">● {diff.summary.unchanged} unchanged</span>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div
            style={{
              fontSize: 11,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              fontFamily: "var(--font-mono)",
            }}
          >
            Current · #{diff.current.id}
          </div>
          <div
            className="mono"
            style={{
              fontSize: 32,
              fontWeight: 600,
              color: scoreColor(diff.current.score),
              lineHeight: 1.1,
              marginTop: 4,
            }}
          >
            {pct(diff.current.score, 1)}
          </div>
          <div className="mono" style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 6 }}>
            {diff.current.model_version ?? "—"} · prompt v{diff.current.prompt_version ?? "—"}
          </div>
        </div>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <div
          style={{
            padding: "14px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600 }}>Per-test deltas</div>
          <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
            {diff.tests.length} tests
          </div>
        </div>
        {diff.tests.map((t, i) => {
          const isRegressed = t.status === "regressed" || t.status === "failed";
          const isImproved = t.status === "improved";
          return (
            <div
              key={t.name}
              style={{
                padding: "14px 16px",
                borderBottom: i === diff.tests.length - 1 ? "none" : "1px solid var(--border)",
                borderLeft: isRegressed
                  ? "2px solid var(--error)"
                  : isImproved
                    ? "2px solid var(--success)"
                    : "2px solid transparent",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>{t.name}</div>
                <StatusPill
                  status={
                    t.status === "regressed" || t.status === "failed"
                      ? "regressed"
                      : t.status === "improved" || t.status === "passed"
                        ? "improved"
                        : "unchanged"
                  }
                />
              </div>
              <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 12 }}>
                {t.assertions.map((a, j) => (
                  <div
                    key={j}
                    className="mono"
                    style={{
                      fontSize: 11,
                      padding: "4px 8px",
                      border: "1px solid var(--border)",
                      borderRadius: 4,
                      color: "var(--text-dim)",
                      background: "var(--bg-elev)",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>{a.type}</span>{" "}
                    {a.baseline?.score != null && a.current?.score != null && (
                      <>
                        <span style={{ color: scoreColor(a.baseline.score) }}>{pct(a.baseline.score, 0)}</span>
                        {" → "}
                        <span style={{ color: scoreColor(a.current.score) }}>{pct(a.current.score, 0)}</span>
                      </>
                    )}
                    {a.score_delta != null && (
                      <span
                        style={{
                          marginLeft: 6,
                          color: a.score_delta >= 0 ? "var(--success)" : "var(--error)",
                        }}
                      >
                        ({a.score_delta >= 0 ? "+" : ""}
                        {(a.score_delta * 100).toFixed(1)}pp)
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
