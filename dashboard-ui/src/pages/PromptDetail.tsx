import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Breadcrumbs, PageHeader } from "../components/ui";
import { cls, relTime } from "../utils";
import {
  getPromptVersions,
  getPromptDiff,
  getPromptContent,
  savePromptContent,
  getPromptStats,
  getPromptRuns,
  getOnlineDrift,
  lintPromptText,
  promotePrompt,
} from "../api/client";
import type { PromptVersion, DiffResponse, PromptStats, PromptRun, LintFinding, OnlineDrift } from "../api/types";
import { InvocationsPanel } from "../components/InvocationsPanel";
import { TemplateEditor } from "../components/TemplateEditor";

type View = "current" | "diff" | "edit" | "stats" | "evals" | "calls" | "drift";

export default function PromptDetail() {
  const { name = "" } = useParams();
  const promptName = decodeURIComponent(name);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [content, setContent] = useState<string>("");
  const [view, setView] = useState<View>("current");
  const [draft, setDraft] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [stats, setStats] = useState<PromptStats | null>(null);
  const [runs, setRuns] = useState<PromptRun[] | null>(null);
  const [drift, setDrift] = useState<OnlineDrift | null>(null);
  const [variables, setVariables] = useState<string[]>([]);
  const [savedVars, setSavedVars] = useState<string[]>([]);
  const [lint, setLint] = useState<LintFinding[]>([]);

  function reloadVersions(selectLatest = false) {
    return getPromptVersions(promptName)
      .then((r) => {
        // Newest first, so versions[0] is the latest (the API returns asc).
        const sorted = [...r.versions].sort((a, b) => b.version - a.version);
        setVersions(sorted);
        if ((selectLatest || selected == null) && sorted.length > 0) {
          setSelected(sorted[0].version);
        }
      })
      .catch(() => setVersions([]));
  }

  useEffect(() => {
    reloadVersions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptName]);

  // Full content of the selected version — the actual prompt text.
  useEffect(() => {
    if (selected == null) return;
    getPromptContent(promptName, selected)
      .then((r) => {
        setContent(r.content);
        setDraft(r.content);
        setSavedVars(r.variables ?? []);
      })
      .catch(() => setContent(""));
  }, [promptName, selected]);

  // Diff against the previous version (if any).
  useEffect(() => {
    if (selected == null) return;
    const idx = versions.findIndex((v) => v.version === selected);
    const prev = versions[idx + 1];
    if (!prev) {
      setDiff(null);
      return;
    }
    getPromptDiff(promptName, prev.version, selected).then(setDiff).catch(() => setDiff(null));
  }, [promptName, selected, versions]);

  // Per-call distribution stats (loaded when the Stats tab opens).
  useEffect(() => {
    if (view !== "stats" || stats) return;
    getPromptStats(promptName, 30).then(setStats).catch(() => setStats(null));
  }, [view, promptName, stats]);

  // Eval runs that exercised this prompt (loaded when the Evals tab opens).
  useEffect(() => {
    if (view !== "evals" || runs) return;
    getPromptRuns(promptName).then((r) => setRuns(r.runs)).catch(() => setRuns([]));
  }, [view, promptName, runs]);

  // Production-telemetry drift (loaded when the Drift tab opens).
  useEffect(() => {
    if (view !== "drift" || drift) return;
    getOnlineDrift(promptName, 30).then(setDrift).catch(() => setDrift(null));
  }, [view, promptName, drift]);

  // Live lint + variable extraction while editing.
  useEffect(() => {
    if (view !== "edit") return;
    const t = setTimeout(() => {
      lintPromptText(draft)
        .then((r) => {
          setVariables(r.variables);
          setLint(r.lint);
        })
        .catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [view, draft]);

  const isLatest = versions.length > 0 && selected === versions[0].version;
  const prodVersion = versions.find((v) => v.tags?.includes("prod"))?.version ?? null;

  async function handlePromote() {
    if (selected == null) return;
    try {
      await promotePrompt(promptName, selected, "prod");
      await reloadVersions();
    } catch {
      /* ignore */
    }
  }

  async function handleSave() {
    setSaving(true);
    setSaveErr(null);
    try {
      const res = await savePromptContent(promptName, draft);
      await reloadVersions(true);
      setSelected(res.version);
      setView("current");
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const tab = (id: View, label: string, disabled = false) => (
    <button
      onClick={() => !disabled && setView(id)}
      disabled={disabled}
      style={{
        padding: "5px 12px",
        borderRadius: 6,
        fontSize: 12,
        fontWeight: view === id ? 600 : 500,
        cursor: disabled ? "not-allowed" : "pointer",
        color: disabled ? "var(--muted)" : view === id ? "var(--text)" : "var(--text-dim)",
        background: view === id ? "var(--bg-elev)" : "transparent",
        border: view === id ? "1px solid var(--border)" : "1px solid transparent",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {label}
    </button>
  );

  return (
    <div>
      <Breadcrumbs items={[{ label: "prompts", to: "/prompts" }, { label: promptName }]} />
      <PageHeader eyebrow="Prompt" title={promptName} description={`${versions.length} versions tracked.`} />

      <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 12 }}>
        {/* Versions rail */}
        <div className="card" style={{ padding: 8, height: "fit-content" }}>
          <div
            style={{
              padding: "6px 8px",
              fontSize: 11,
              color: "var(--muted)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              fontFamily: "var(--font-mono)",
            }}
          >
            Versions
          </div>
          {versions.map((v, i) => (
            <div
              key={v.version}
              onClick={() => setSelected(v.version)}
              style={{
                padding: "9px 10px",
                borderRadius: 6,
                cursor: "pointer",
                marginBottom: 2,
                background: selected === v.version ? "var(--bg-elev)" : "transparent",
                border: selected === v.version ? "1px solid var(--border)" : "1px solid transparent",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 13, color: "var(--text)", fontWeight: 600 }}>
                  v{v.version}
                  {i === 0 && (
                    <span style={{ fontSize: 9, color: "var(--success)", marginLeft: 6, letterSpacing: "0.04em" }}>
                      LATEST
                    </span>
                  )}
                  {v.tags?.includes("prod") && (
                    <span style={{ fontSize: 9, color: "var(--accent)", marginLeft: 6, letterSpacing: "0.04em" }}>
                      PROD
                    </span>
                  )}
                </span>
                <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>
                  {v.hash.slice(0, 7)}
                </span>
              </div>
              <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 3, fontFamily: "var(--font-mono)" }}>
                {relTime(v.created_at)}
              </div>
            </div>
          ))}
          {versions.length === 0 && (
            <div style={{ padding: 12, color: "var(--muted)", fontSize: 12 }}>Loading…</div>
          )}
        </div>

        {/* Content panel */}
        <div className="card" style={{ padding: 0 }}>
          <div
            style={{
              padding: "12px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", gap: 4 }}>
              {tab("current", selected != null ? `Current · v${selected}` : "Current")}
              {tab("diff", "Diff", !diff)}
              {tab("edit", "Edit")}
              {tab("stats", "Stats")}
              {tab("calls", "Invocations")}
              {tab("evals", "Evals")}
              {tab("drift", "Drift")}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {view === "diff" && diff && (
                <div style={{ display: "flex", gap: 6 }}>
                  <span className="chip mono" style={{ fontSize: 10, color: "var(--error)" }}>− {diff.deletions}</span>
                  <span className="chip mono" style={{ fontSize: 10, color: "var(--success)" }}>+ {diff.additions}</span>
                </div>
              )}
              {selected != null && selected !== prodVersion && (
                <button
                  onClick={handlePromote}
                  title="Point the prod env tag at this version (render_prompt(env='prod') will serve it)"
                  style={{
                    padding: "5px 11px", borderRadius: 6, fontSize: 11.5, fontWeight: 600,
                    cursor: "pointer", color: "var(--bg)", background: "var(--accent)", border: "none",
                  }}
                >
                  Promote v{selected} → prod
                </button>
              )}
              {selected != null && selected === prodVersion && (
                <span className="chip mono" style={{ fontSize: 10, color: "var(--accent)", borderColor: "var(--accent-line)", background: "var(--accent-soft)" }}>
                  serving in prod
                </span>
              )}
            </div>
          </div>

          {/* CURRENT — the actual prompt text */}
          {view === "current" && (
            <pre
              style={{
                margin: 0,
                padding: "16px 18px",
                fontSize: 12.5,
                lineHeight: 1.6,
                color: "var(--text)",
                fontFamily: "var(--font-mono)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {content || "Loading…"}
            </pre>
          )}

          {/* DIFF */}
          {view === "diff" && (
            <div style={{ padding: "8px 0" }}>
              {!diff ? (
                <div style={{ padding: 24, color: "var(--muted)", fontSize: 12, textAlign: "center" }}>
                  Earliest version — no diff available.
                </div>
              ) : (
                diff.lines.map((l, i) => (
                  <div key={i} className={cls("diff-line", l.type)}>
                    <span className="ln">{l.old_num ?? ""}</span>
                    <span className="ln">{l.new_num ?? ""}</span>
                    <pre
                      style={{
                        color:
                          l.type === "added"
                            ? "var(--success)"
                            : l.type === "deleted"
                              ? "var(--error)"
                              : "var(--text-dim)",
                      }}
                    >
                      {l.type === "added" ? "+ " : l.type === "deleted" ? "- " : "  "}
                      {l.content || " "}
                    </pre>
                  </div>
                ))
              )}
            </div>
          )}

          {/* EDIT — save creates a new version the app picks up live */}
          {view === "edit" && (
            <div style={{ padding: 16 }}>
              {!isLatest && (
                <div
                  style={{
                    fontSize: 11.5,
                    color: "var(--muted)",
                    marginBottom: 10,
                    padding: "8px 10px",
                    borderRadius: 6,
                    background: "var(--bg-elev)",
                  }}
                >
                  Editing from v{selected}. Saving appends a new latest version (it won't overwrite history).
                </div>
              )}
              <TemplateEditor value={draft} onChange={setDraft} minHeight={360} />

              {/* Template variables — color-coded against the saved set.
                  The app supplies a FIXED set of variables, so adding/removing
                  one here breaks rendering until the code is updated. */}
              {(() => {
                const added = variables.filter((v) => !savedVars.includes(v));
                const removed = savedVars.filter((v) => !variables.includes(v));
                const changed = added.length > 0 || removed.length > 0;
                return (
                  <>
                    {(variables.length > 0 || removed.length > 0) && (
                      <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>variables:</span>
                        {variables.map((v) => {
                          const isNew = added.includes(v);
                          return (
                            <span key={v} className="chip mono" style={{
                              fontSize: 10.5,
                              color: isNew ? "var(--warning)" : "var(--accent)",
                              borderColor: isNew ? "var(--warning)" : "var(--accent-line)",
                              background: isNew ? "transparent" : "var(--accent-soft)",
                            }}>
                              ${v}{isNew ? " (new)" : ""}
                            </span>
                          );
                        })}
                        {removed.map((v) => (
                          <span key={v} className="chip mono" style={{ fontSize: 10.5, color: "var(--error)", borderColor: "var(--error)", textDecoration: "line-through" }}>
                            ${v}
                          </span>
                        ))}
                      </div>
                    )}
                    {changed && (
                      <div style={{ marginTop: 10, padding: "9px 12px", borderRadius: 7, border: "1px solid var(--warning)", background: "color-mix(in oklch, var(--warning) 12%, transparent)", fontSize: 11.5, color: "var(--text-dim)" }}>
                        <span style={{ color: "var(--warning)", fontWeight: 600 }}>Variable set changed.</span>{" "}
                        The app fills a fixed set of variables in code, so
                        {added.length > 0 && <> adding <span className="mono">{added.map((v) => "$" + v).join(", ")}</span></>}
                        {added.length > 0 && removed.length > 0 && " and"}
                        {removed.length > 0 && <> removing <span className="mono">{removed.map((v) => "$" + v).join(", ")}</span></>}
                        {" "}won't take effect (and may break rendering) until the calling code is updated to match. Edit the wording freely — just keep the same <span className="mono">$variables</span>.
                      </div>
                    )}
                  </>
                );
              })()}
              {lint.length > 0 && (
                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 5 }}>
                  {lint.map((f, i) => {
                    const c = f.level === "error" ? "var(--error)" : f.level === "warning" ? "var(--warning)" : "var(--text-dim)";
                    return (
                      <div key={i} style={{ display: "flex", gap: 8, fontSize: 11.5, color: c, alignItems: "baseline" }}>
                        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, textTransform: "uppercase", flexShrink: 0 }}>{f.level}</span>
                        <span style={{ color: "var(--text-dim)" }}>{f.message}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                <button
                  onClick={handleSave}
                  disabled={saving || !draft.trim() || draft === content}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 7,
                    fontSize: 12.5,
                    fontWeight: 600,
                    cursor: saving || draft === content ? "not-allowed" : "pointer",
                    color: "var(--bg)",
                    background: draft === content || !draft.trim() ? "var(--muted)" : "var(--accent)",
                    border: "none",
                    opacity: saving ? 0.7 : 1,
                  }}
                >
                  {saving ? "Saving…" : "Save as new version"}
                </button>
                {draft !== content && (
                  <button
                    onClick={() => setDraft(content)}
                    style={{
                      padding: "8px 14px",
                      borderRadius: 7,
                      fontSize: 12.5,
                      cursor: "pointer",
                      color: "var(--text-dim)",
                      background: "transparent",
                      border: "1px solid var(--border)",
                    }}
                  >
                    Reset
                  </button>
                )}
                {saveErr && <span style={{ fontSize: 11.5, color: "var(--error)" }}>{saveErr}</span>}
              </div>
            </div>
          )}

          {/* STATS — per-call distribution over the last 30 days */}
          {view === "stats" && <StatsPanel stats={stats} />}

          {/* EVALS — which eval runs exercised this prompt */}
          {view === "evals" && <EvalsPanel runs={runs} />}

          {/* INVOCATIONS — this prompt's calls (recent), with ratings → trace */}
          {view === "calls" && (
            <div style={{ padding: 16 }}>
              <InvocationsPanel name={promptName} order="recent" days={30}
                crumbs={[{ label: "prompts", to: "/prompts" }, { label: promptName, to: `/prompts/${encodeURIComponent(promptName)}` }]}
                emptyHint="No invocations for this prompt in the last 30 days." />
            </div>
          )}

          {/* DRIFT — distribution shift in live telemetry (recent half vs older half) */}
          {view === "drift" && <DriftPanel drift={drift} />}
        </div>
      </div>
    </div>
  );
}

/* -------- production-telemetry drift -------- */
function fmtMean(metric: string, v: number): string {
  if (metric === "cost") return "$" + v.toFixed(4);
  if (metric === "latency_ms") return Math.round(v) + "ms";
  if (metric === "rating") return v.toFixed(2);
  return Math.round(v).toString();
}

function DriftPanel({ drift }: { drift: OnlineDrift | null }) {
  if (!drift) return <div style={{ padding: 24, color: "var(--muted)", fontSize: 12 }}>Loading…</div>;
  if (drift.status === "insufficient")
    return (
      <div style={{ padding: 24, color: "var(--muted)", fontSize: 12.5, textAlign: "center" }}>
        Not enough production traffic to analyse drift — needs a handful of recorded
        invocations for this prompt in the last {drift.days} days.
      </div>
    );
  const sevColor = (s: string) =>
    s === "high" ? "var(--error)" : s === "medium" ? "var(--warning)" : "var(--muted)";
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <span style={{ width: 9, height: 9, borderRadius: 999, background: drift.drifting_count ? "var(--error)" : "var(--success)" }} />
        <div style={{ fontSize: 13, fontWeight: 600 }}>
          {drift.drifting_count
            ? `${drift.drifting_count} metric${drift.drifting_count > 1 ? "s" : ""} drifting`
            : "Stable"}
        </div>
        <div className="mono" style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
          {drift.total_calls} calls · last {drift.days}d · recent vs older half
        </div>
      </div>
      <table className="pr">
        <thead>
          <tr>
            <th></th><th>Metric</th><th className="r">Baseline</th><th className="r">Recent</th>
            <th className="r">Change</th><th className="r">p</th><th>Flag</th>
          </tr>
        </thead>
        <tbody>
          {drift.metrics.map((m) => (
            <tr key={m.metric}>
              <td style={{ width: 24, textAlign: "center" }}>
                <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 999, background: sevColor(m.severity) }} />
              </td>
              <td style={{ fontWeight: 600, fontSize: 13 }}>{m.label}</td>
              <td className="r mono" style={{ color: "var(--text-dim)" }}>{fmtMean(m.metric, m.baseline_mean)}</td>
              <td className="r mono" style={{ color: "var(--text-dim)" }}>{fmtMean(m.metric, m.recent_mean)}</td>
              <td className="r mono" style={{ fontWeight: 600, color: m.drifting ? sevColor(m.severity) : "var(--text-dim)" }}>
                {m.pct_change == null ? "n/a" : (m.pct_change >= 0 ? "+" : "") + (m.pct_change * 100).toFixed(0) + "%"}
              </td>
              <td className="r mono" style={{ color: "var(--text-dim)" }}>{m.p_value == null ? "—" : m.p_value.toFixed(3)}</td>
              <td>
                {m.drifting
                  ? <span className="chip mono" style={{ fontSize: 10, color: sevColor(m.severity) }}>{m.severity}</span>
                  : <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 10 }}>
        Flagged only when a metric moves ≥10% toward its bad direction (cost, latency, length up;
        rating down). p from a Mann-Whitney U test comparing the two halves.
      </div>
    </div>
  );
}

/* -------- eval runs that exercised this prompt -------- */
function EvalsPanel({ runs }: { runs: PromptRun[] | null }) {
  if (!runs) return <div style={{ padding: 24, color: "var(--muted)", fontSize: 12 }}>Loading…</div>;
  if (runs.length === 0)
    return (
      <div style={{ padding: 24, color: "var(--muted)", fontSize: 12.5, textAlign: "center" }}>
        No eval runs have referenced this prompt yet. Link a suite by passing
        <span className="mono" style={{ color: "var(--text-dim)" }}> prompt_name</span> when you record a run.
      </div>
    );
  return (
    <table className="pr">
      <thead>
        <tr>
          <th></th><th>Suite</th><th className="r">Prompt v</th><th>Model</th>
          <th className="r">Score</th><th>When</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.run_id}>
            <td style={{ width: 24, textAlign: "center" }}>
              <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 999, background: r.passed ? "var(--success)" : "var(--error)" }} />
            </td>
            <td style={{ fontWeight: 600, fontSize: 13 }}>{r.suite_name}</td>
            <td className="r mono" style={{ color: "var(--text-dim)" }}>{r.prompt_version != null ? `v${r.prompt_version}` : "—"}</td>
            <td className="mono" style={{ color: "var(--text-dim)", fontSize: 12 }}>{r.model_version ?? "—"}</td>
            <td className="r mono" style={{ fontWeight: 600, color: r.passed ? "var(--success)" : "var(--error)" }}>
              {r.score != null ? (r.score * 100).toFixed(0) + "%" : "—"}
            </td>
            <td className="mono" style={{ color: "var(--secondary)", fontSize: 11 }}>{relTime(r.timestamp)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* -------- per-call distribution panel -------- */
function StatsPanel({ stats }: { stats: PromptStats | null }) {
  if (!stats) return <div style={{ padding: 24, color: "var(--muted)", fontSize: 12 }}>Loading…</div>;
  if (stats.count === 0)
    return (
      <div style={{ padding: 24, color: "var(--muted)", fontSize: 12, textAlign: "center" }}>
        No invocations recorded in the last {stats.days} days.
      </div>
    );

  const m = stats.metrics;
  const fmtTok = (n: number) => Math.round(n).toLocaleString();
  const fmtCost = (n: number) => "$" + n.toFixed(n < 0.01 ? 5 : 4);
  const fmtMs = (n: number) => (n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms");

  const cards = [
    { label: "calls (30d)", value: stats.count.toLocaleString() },
    { label: "avg cost / call", value: fmtCost(m.cost.avg) },
    { label: "total cost", value: fmtCost(m.cost.sum) },
    { label: "avg input tok", value: fmtTok(m.tokens_in.avg) },
    { label: "avg output tok", value: fmtTok(m.tokens_out.avg) },
    { label: "avg latency", value: fmtMs(m.latency_ms.avg) },
  ];

  const rows: { k: string; s: typeof m.tokens_in; f: (n: number) => string }[] = [
    { k: "Input tokens (sent, incl. payload)", s: m.tokens_in, f: fmtTok },
    { k: "Output tokens (response)", s: m.tokens_out, f: fmtTok },
    { k: "Cost", s: m.cost, f: fmtCost },
    { k: "Latency", s: m.latency_ms, f: fmtMs },
  ];

  const maxBar = Math.max(1, ...stats.histogram.map((h) => h.count));

  return (
    <div style={{ padding: 16 }}>
      {/* summary cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8, marginBottom: 18 }}>
        {cards.map((c) => (
          <div key={c.label} style={{ padding: "10px 12px", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8 }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontFamily: "var(--font-mono)" }}>{c.label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text)", marginTop: 3 }}>{c.value}</div>
          </div>
        ))}
      </div>

      {/* percentile table */}
      <table className="pr" style={{ marginBottom: 18 }}>
        <thead>
          <tr>
            <th>Metric</th><th className="r">min</th><th className="r">p50</th>
            <th className="r">avg</th><th className="r">p95</th><th className="r">max</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.k}>
              <td style={{ fontSize: 12.5, color: "var(--text-dim)" }}>{r.k}</td>
              <td className="r mono" style={{ fontSize: 12 }}>{r.f(r.s.min)}</td>
              <td className="r mono" style={{ fontSize: 12 }}>{r.f(r.s.p50)}</td>
              <td className="r mono" style={{ fontSize: 12, color: "var(--text)" }}>{r.f(r.s.avg)}</td>
              <td className="r mono" style={{ fontSize: 12 }}>{r.f(r.s.p95)}</td>
              <td className="r mono" style={{ fontSize: 12 }}>{r.f(r.s.max)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* input-size distribution curve */}
      <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)", marginBottom: 8 }}>
        Input size distribution (tokens / call)
      </div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 120 }}>
        {stats.histogram.map((h, i) => (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }} title={`${h.start}–${h.end} tok: ${h.count} calls`}>
            <div
              style={{
                width: "100%",
                height: `${(h.count / maxBar) * 96}px`,
                minHeight: h.count > 0 ? 2 : 0,
                background: "var(--accent)",
                borderRadius: "3px 3px 0 0",
                opacity: 0.85,
              }}
            />
            <span style={{ fontSize: 8.5, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>{h.start}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
