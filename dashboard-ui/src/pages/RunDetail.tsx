import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Breadcrumbs, KPI, PageHeader, StatusPill } from "../components/ui";
import { pct, scoreColor } from "../utils";
import { getRunDetail } from "../api/client";
import type { RunDetailResponse } from "../api/types";

export default function RunDetail() {
  const { name = "", runId = "0" } = useParams();
  const suiteName = decodeURIComponent(name);
  const id = parseInt(runId, 10);
  const [data, setData] = useState<RunDetailResponse | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<"all" | "failed" | "passed">("all");

  useEffect(() => {
    getRunDetail(suiteName, id)
      .then(setData)
      .catch(() => setData(null));
  }, [suiteName, id]);

  if (!data) {
    return (
      <div>
        <Breadcrumbs
          items={[
            { label: "overview", to: "/" },
            { label: suiteName, to: `/suite/${encodeURIComponent(suiteName)}` },
            { label: `run #${id}` },
          ]}
        />
        <PageHeader eyebrow="Run" title={`#${id}`} description="Loading…" />
      </div>
    );
  }

  const { run, assertions } = data;
  const filtered = assertions.filter((a) =>
    filter === "all" ? true : filter === "passed" ? a.passed : !a.passed
  );
  const passedCount = assertions.filter((a) => a.passed).length;

  return (
    <div>
      <Breadcrumbs
        items={[
          { label: "overview", to: "/" },
          { label: suiteName, to: `/suite/${encodeURIComponent(suiteName)}` },
          { label: `run #${run.id}` },
        ]}
      />

      <PageHeader
        eyebrow={`Run · ${new Date(run.timestamp).toLocaleString()}`}
        title={`#${run.id}`}
        tags={run.overall_pass ? [] : ["FAIL"]}
        description={`${passedCount}/${assertions.length} assertions passed · ${run.model_version ?? "—"} · prompt v${run.prompt_version ?? "—"}`}
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <KPI
          label="Score"
          value={pct(run.overall_score, 1)}
          accent={scoreColor(run.overall_score)}
          sub={run.overall_pass ? "pass" : "fail"}
        />
        <KPI
          label="Assertions"
          value={`${passedCount}/${assertions.length}`}
          accent="var(--text)"
          sub="passed"
        />
        <KPI label="Model" value={run.model_version ?? "—"} accent="var(--text-dim)" sub="runtime" />
        <KPI
          label="Prompt"
          value={run.prompt_version != null ? `v${run.prompt_version}` : "—"}
          accent="var(--text-dim)"
          sub={run.prompt_name ?? "—"}
        />
      </div>

      {data.judge && (
        <div className="card" style={{ padding: "10px 14px", marginBottom: 20, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontFamily: "var(--font-mono)" }}>Judge cost</span>
          <span style={{ fontSize: 15, fontWeight: 600 }}>
            {data.judge.unpriced && data.judge.cost === 0 ? "—" : "$" + data.judge.cost.toFixed(4)}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-dim)" }}>
            {data.judge.calls} judge call{data.judge.calls === 1 ? "" : "s"} · {data.judge.tokens_in + data.judge.tokens_out} tok
            {data.judge.model ? ` · ${data.judge.model}` : ""}
          </span>
          <span style={{ fontSize: 10.5, color: "var(--muted)", marginLeft: "auto" }}>
            {data.judge.unpriced
              ? (data.judge.model ? `no pricing for ${data.judge.model}` : "set [judge] model in config to price")
              : "estimated (~4 chars/token)"}
          </span>
        </div>
      )}

      <div className="card" style={{ overflow: "hidden" }}>
        <div
          style={{
            padding: "14px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600 }}>Assertions</div>
          <div
            style={{
              display: "inline-flex",
              gap: 4,
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: 2,
            }}
          >
            {(["all", "failed", "passed"] as const).map((x) => (
              <button
                key={x}
                onClick={() => setFilter(x)}
                style={{
                  padding: "3px 10px",
                  fontSize: 11,
                  borderRadius: 4,
                  background: filter === x ? "var(--surface)" : "transparent",
                  border: "1px solid " + (filter === x ? "var(--border)" : "transparent"),
                  color: filter === x ? "var(--text)" : "var(--secondary)",
                  cursor: "pointer",
                  fontFamily: "var(--font-ui)",
                }}
              >
                {x}
              </button>
            ))}
          </div>
        </div>
        {filtered.map((a, i) => {
          const isOpen = expanded.has(a.id);
          return (
            <div key={a.id} style={{ borderBottom: i === filtered.length - 1 ? "none" : "1px solid var(--border)" }}>
              <div
                onClick={() => {
                  const n = new Set(expanded);
                  if (n.has(a.id)) n.delete(a.id);
                  else n.add(a.id);
                  setExpanded(n);
                }}
                style={{
                  display: "grid",
                  gridTemplateColumns: "64px 150px 1fr 80px 60px 16px",
                  gap: 12,
                  alignItems: "center",
                  padding: "10px 16px",
                  cursor: "pointer",
                  borderLeft: a.passed ? "2px solid transparent" : "2px solid var(--error)",
                }}
              >
                <span
                  style={{
                    color: a.passed ? "var(--success)" : "var(--error)",
                    fontWeight: 700,
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {a.passed ? "PASS" : "FAIL"}
                </span>
                <span style={{ color: "var(--text-dim)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
                  {a.assertion_type}
                </span>
                <span
                  style={{
                    color: "var(--text)",
                    fontSize: 13,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {a.test_name}
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div
                    style={{
                      flex: 1,
                      height: 4,
                      background: "var(--bg-elev)",
                      borderRadius: 2,
                      overflow: "hidden",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <div
                      style={{
                        width: ((a.score ?? 0) * 100) + "%",
                        height: "100%",
                        background: scoreColor(a.score),
                      }}
                    />
                  </div>
                </div>
                <span
                  className="mono"
                  style={{
                    fontSize: 12,
                    color: scoreColor(a.score),
                    fontWeight: 600,
                    textAlign: "right",
                  }}
                >
                  {pct(a.score, 0)}
                </span>
                <span style={{ color: "var(--muted)", textAlign: "right", fontSize: 11 }}>
                  {isOpen ? "▾" : "▸"}
                </span>
              </div>
              {isOpen && (
                <div
                  className="mono enter"
                  style={{
                    padding: "12px 16px 16px 82px",
                    background: "rgba(0,0,0,0.2)",
                    fontSize: 11.5,
                    color: "var(--text-dim)",
                    lineHeight: 1.7,
                    borderTop: "1px solid var(--border)",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  <div style={{ color: "var(--muted)", marginBottom: 6 }}>
                    $ promptry assertion --id {a.id}
                  </div>
                  {a.details ? (
                    <pre style={{ margin: 0, color: "var(--text-dim)" }}>
                      {JSON.stringify(a.details, null, 2)}
                    </pre>
                  ) : (
                    <div style={{ color: "var(--muted)" }}>(no details)</div>
                  )}
                  {a.latency_ms != null && (
                    <div style={{ marginTop: 6, color: "var(--muted)" }}>
                      latency: {a.latency_ms}ms
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div style={{ padding: 24, color: "var(--muted)", textAlign: "center", fontSize: 13 }}>
            No assertions match this filter.
          </div>
        )}
      </div>
    </div>
  );
}
