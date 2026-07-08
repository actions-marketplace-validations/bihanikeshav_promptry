import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/ui";
import { getNearDuplicates, getPromptDiff2 } from "../api/client";
import type { NearDuplicatePair, NearDuplicates, Diff2Response } from "../api/types";

/** The similarity of a pair, tolerating either the fixed-contract `score`
 *  field or the legacy `similarity` field. */
function pairScore(p: NearDuplicatePair): number {
  return p.score ?? p.similarity ?? 0;
}

function DiffView({ diff }: { diff: Diff2Response["diff"] }) {
  return (
    <pre
      className="mono"
      style={{
        margin: 0,
        fontSize: 12.5,
        lineHeight: 1.7,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: 12,
        maxHeight: 440,
        overflow: "auto",
      }}
    >
      {diff.map((seg, i) => {
        if (seg.type === "insert") {
          return (
            <span
              key={i}
              style={{ background: "var(--success-soft)", color: "var(--success)", borderRadius: 2 }}
            >
              {seg.text}
            </span>
          );
        }
        if (seg.type === "delete") {
          return (
            <span
              key={i}
              style={{
                background: "var(--error-soft)",
                color: "var(--error)",
                textDecoration: "line-through",
                borderRadius: 2,
              }}
            >
              {seg.text}
            </span>
          );
        }
        return (
          <span key={i} style={{ color: "var(--text-dim)" }}>
            {seg.text}
          </span>
        );
      })}
    </pre>
  );
}

export default function Duplicates() {
  const navigate = useNavigate();
  const [data, setData] = useState<NearDuplicates | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [diff, setDiff] = useState<Diff2Response | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffErr, setDiffErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getNearDuplicates()
      .then((d) => {
        setData(d);
        setErr(null);
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const pairs = useMemo(
    () =>
      (data?.pairs ?? [])
        .slice()
        .sort((a, b) => pairScore(b) - pairScore(a)),
    [data]
  );

  const selectPair = (idx: number, p: NearDuplicatePair) => {
    setSelected(idx);
    setDiff(null);
    setDiffErr(null);
    setDiffLoading(true);
    getPromptDiff2(p.a, p.b)
      .then((d) => setDiff(d))
      .catch((e) => setDiffErr(String(e)))
      .finally(() => setDiffLoading(false));
  };

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · duplicates"
        title="Near-duplicate consolidation"
        description="Prompts that have drifted into near-copies of each other. Consolidate them onto one canonical name to cut maintenance and unlock prompt caching on the shared prefix."
        tags={data ? [data.mode, `${pairs.length} pair${pairs.length === 1 ? "" : "s"}`] : undefined}
      />

      <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 0.8fr) minmax(0, 1.6fr)", gap: 14 }}>
        {/* LEFT: pair list */}
        <div className="card" style={{ padding: 0, overflow: "hidden", alignSelf: "start" }}>
          <div
            style={{
              padding: "10px 14px",
              borderBottom: "1px solid var(--border)",
              background: "var(--bg-elev)",
              fontSize: 10,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--muted)",
              fontFamily: "var(--font-mono)",
            }}
          >
            Candidate pairs
            {data && (
              <span style={{ marginLeft: 8, color: "var(--text-dim)" }}>
                ≥ {Math.round(data.threshold * 100)}%
              </span>
            )}
          </div>

          {loading && (
            <div style={{ padding: 14 }}>
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton" style={{ height: 34, borderRadius: 6, marginBottom: 8 }} />
              ))}
            </div>
          )}

          {err && (
            <div style={{ padding: 16, fontSize: 12, color: "var(--error)", fontFamily: "var(--font-mono)" }}>
              {err}
            </div>
          )}

          {!loading && !err && pairs.length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
              No near-duplicate prompts found. Nice — your registry is lean.
            </div>
          )}

          {pairs.map((p, i) => {
            const active = selected === i;
            return (
              <button
                key={`${p.a}|${p.b}`}
                onClick={() => selectPair(i, p)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "10px 14px",
                  border: "none",
                  borderBottom: "1px solid var(--border)",
                  borderLeft: "2px solid " + (active ? "var(--accent)" : "transparent"),
                  background: active ? "var(--surface)" : "transparent",
                  cursor: "pointer",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    className="mono"
                    style={{ fontSize: 12, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {p.a}
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 12, color: "var(--secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {p.b}
                  </div>
                </div>
                <span
                  className="mono"
                  style={{
                    flexShrink: 0,
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "2px 7px",
                    borderRadius: 4,
                    background: "var(--warning-soft)",
                    color: "var(--warning)",
                  }}
                >
                  {Math.round(pairScore(p) * 100)}%
                </span>
              </button>
            );
          })}
        </div>

        {/* RIGHT: diff + panels */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          {selected == null && (
            <div
              className="card"
              style={{
                padding: 40,
                textAlign: "center",
                color: "var(--muted)",
                border: "1px dashed var(--border-strong)",
                background: "transparent",
                fontSize: 12.5,
              }}
            >
              Select a pair to see the content diff, cache optimization, and consolidation guidance.
            </div>
          )}

          {selected != null && diffLoading && (
            <div className="card" style={{ padding: 14 }}>
              <div className="skeleton" style={{ height: 12, width: "70%", borderRadius: 3, marginBottom: 10 }} />
              <div className="skeleton" style={{ height: 120, borderRadius: 6 }} />
            </div>
          )}

          {selected != null && diffErr && (
            <div className="card" style={{ padding: 16, fontSize: 12, color: "var(--error)", fontFamily: "var(--font-mono)" }}>
              {diffErr}
            </div>
          )}

          {diff && !diffLoading && (
            <>
              {/* Diff card */}
              <div className="card enter" style={{ padding: 14 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
                  <div style={{ display: "flex", gap: 14, alignItems: "center", minWidth: 0 }}>
                    <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>
                      {diff.a.name}
                      <span style={{ color: "var(--muted)", fontWeight: 400 }}> v{diff.a.latest_version}</span>
                    </span>
                    <span style={{ color: "var(--muted)" }}>↔</span>
                    <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>
                      {diff.b.name}
                      <span style={{ color: "var(--muted)", fontWeight: 400 }}> v{diff.b.latest_version}</span>
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 12, fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
                    <span><span style={{ color: "var(--text-dim)" }}>■</span> equal</span>
                    <span><span style={{ color: "var(--success)" }}>■</span> in {diff.b.name}</span>
                    <span><span style={{ color: "var(--error)" }}>■</span> only {diff.a.name}</span>
                  </div>
                </div>
                <DiffView diff={diff.diff} />
                <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button
                    className="btn"
                    onClick={() => navigate(`/playground?prompt=${encodeURIComponent(diff.a.name)}`)}
                  >
                    Test {diff.a.name} in Playground
                  </button>
                  <button
                    className="btn"
                    onClick={() => navigate(`/playground?prompt=${encodeURIComponent(diff.b.name)}`)}
                  >
                    Test {diff.b.name} in Playground
                  </button>
                </div>
              </div>

              {/* Cache optimization panel */}
              <div className="card enter" style={{ padding: 14 }}>
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    marginBottom: 12,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  Cache optimization
                </div>
                <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span
                      className="mono"
                      style={{ fontSize: 30, fontWeight: 600, color: "var(--accent)", lineHeight: 1 }}
                    >
                      {Math.round(diff.shared_prefix_ratio * 100)}%
                    </span>
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>
                      shared prefix · {diff.shared_prefix_chars.toLocaleString()} chars
                    </span>
                  </div>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <div
                      style={{
                        height: 8,
                        borderRadius: 4,
                        background: "var(--bg-elev)",
                        overflow: "hidden",
                        marginBottom: 10,
                      }}
                    >
                      <div
                        style={{
                          width: `${Math.min(100, Math.round(diff.shared_prefix_ratio * 100))}%`,
                          height: "100%",
                          background: "var(--accent)",
                        }}
                      />
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "flex-start",
                        fontSize: 12.5,
                        color: diff.cache_suggestion.suggested ? "var(--text-dim)" : "var(--secondary)",
                      }}
                    >
                      <span
                        className="pill pill-dot"
                        style={{
                          flexShrink: 0,
                          color: diff.cache_suggestion.suggested ? "var(--success)" : "var(--secondary)",
                          background: diff.cache_suggestion.suggested ? "var(--success-soft)" : "rgba(255,255,255,0.04)",
                          border: `1px solid ${diff.cache_suggestion.suggested ? "var(--success)22" : "var(--border)"}`,
                        }}
                      >
                        {diff.cache_suggestion.suggested ? "cache" : "skip"}
                      </span>
                      <span>{diff.cache_suggestion.rationale}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Consolidation guidance panel */}
              <div className="card enter" style={{ padding: 14 }}>
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    marginBottom: 10,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  Consolidation guidance
                </div>
                <div style={{ fontSize: 12.5, color: "var(--text-dim)", lineHeight: 1.65 }}>
                  <p style={{ margin: "0 0 8px" }}>
                    Keep one canonical name — <span className="mono" style={{ color: "var(--accent)" }}>{diff.a.name}</span>{" "}
                    reads as the more general of the two — and retire{" "}
                    <span className="mono" style={{ color: "var(--text)" }}>{diff.b.name}</span>.
                  </p>
                  <p style={{ margin: "0 0 8px", color: "var(--secondary)" }}>
                    This tool does not merge or delete anything. Any code that calls{" "}
                    <span className="mono" style={{ color: "var(--text)" }}>track("{diff.b.name}")</span> (or references
                    it in a suite) must be updated by a developer to point at{" "}
                    <span className="mono" style={{ color: "var(--accent)" }}>{diff.a.name}</span> before the duplicate
                    stops receiving traffic.
                  </p>
                  <ul style={{ margin: "8px 0 0", paddingLeft: 18, color: "var(--secondary)" }}>
                    <li>Grep the codebase for both prompt names to find every call-site.</li>
                    <li>Fold any unique wording (highlighted above) into the canonical prompt first.</li>
                    <li>Re-run affected eval suites after switching the name.</li>
                  </ul>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
