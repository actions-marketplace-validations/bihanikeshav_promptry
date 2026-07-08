import { useEffect, useMemo, useState, type ReactNode, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/ui";
import {
  getCacheAnalysisList,
  getPromptCacheAnalysis,
  getNearDuplicates,
  getPromptDiff2,
  getShortenAnalysisList,
  getPromptShortenAnalysis,
  getConfig,
  savePromptContent,
} from "../api/client";
import type {
  CacheAnalysisRow,
  PromptCacheAnalysis,
  CacheRecommendation,
  NearDuplicatePair,
  NearDuplicates,
  Diff2Response,
  ShortenRow,
  PromptShortenAnalysis,
  ShortenFinding,
  ShortenKind,
} from "../api/types";

function pairScore(p: NearDuplicatePair): number {
  return p.score ?? p.similarity ?? 0;
}

type Mode = "reorder" | "consolidate" | "shorten";
const MODES: { id: Mode; label: string; soon?: boolean }[] = [
  { id: "reorder", label: "Reorder inputs" },
  { id: "consolidate", label: "Consolidate" },
  { id: "shorten", label: "Shorten" },
];

function ModeToggle({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  return (
    <div style={{ display: "inline-flex", gap: 2, padding: 3, borderRadius: 8, background: "var(--bg-elev)", border: "1px solid var(--border)", marginBottom: 18 }}>
      {MODES.map((m) => {
        const active = mode === m.id;
        return (
          <button
            key={m.id}
            onClick={() => !m.soon && setMode(m.id)}
            disabled={m.soon}
            title={m.soon ? "Coming soon" : undefined}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "5px 12px",
              borderRadius: 6,
              border: "none",
              fontSize: 12,
              fontWeight: active ? 600 : 500,
              cursor: m.soon ? "default" : "pointer",
              color: active ? "var(--text)" : m.soon ? "var(--muted)" : "var(--secondary)",
              background: active ? "var(--surface)" : "transparent",
            }}
          >
            {m.label}
            {m.soon && (
              <span className="mono" style={{ fontSize: 8.5, textTransform: "uppercase", letterSpacing: "0.05em", padding: "1px 4px", borderRadius: 3, background: "rgba(255,255,255,0.06)", color: "var(--muted)" }}>
                soon
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

const REC_LABEL: Record<CacheRecommendation, string> = {
  move_inputs_to_end: "reorder",
  already_optimal: "optimal",
  too_small: "too small",
  no_variables: "static",
};

function recTone(rec: CacheRecommendation): { fg: string; bg: string } {
  if (rec === "move_inputs_to_end") return { fg: "var(--warning)", bg: "var(--warning-soft)" };
  if (rec === "too_small") return { fg: "var(--muted)", bg: "rgba(255,255,255,0.04)" };
  return { fg: "var(--success)", bg: "var(--success-soft)" };
}

function tok(n: number): string {
  return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n);
}

const SHORTEN_KIND: Record<ShortenKind, { label: string; color: string }> = {
  duplicate: { label: "repeat", color: "var(--warning)" },
  semantic_duplicate: { label: "semantic", color: "var(--accent)" },
  filler: { label: "filler", color: "var(--secondary)" },
  redundant_format: { label: "format", color: "var(--warning)" },
  whitespace: { label: "space", color: "var(--muted)" },
};

// Distinct hues so each redundancy pair (a span + its original) is visually
// its own colour; translucent fills read on both light and dark themes.
const PAIR_COLORS = ["#e0a458", "#9b8cff", "#4fb0c6", "#e0699f", "#6cc070", "#d98b5f"];

type Variant = "solid" | "dashed" | "filler";
function variantStyle(color: string | null, variant: Variant): CSSProperties {
  if (variant === "filler")
    return { textDecoration: "underline dashed", textUnderlineOffset: 3, textDecorationColor: "var(--muted)", color: "var(--muted)", fontStyle: "italic" };
  if (variant === "dashed")
    return { textDecoration: "underline dashed", textUnderlineOffset: 3, textDecorationColor: color || "var(--muted)", color: "var(--text-dim)" };
  return { background: `color-mix(in oklch, ${color} 24%, transparent)`, color: color || "var(--text)", borderRadius: 2 };
}

/** Render the prompt with each finding highlighted in place. Each duplicate /
 *  semantic pair gets its own colour (via `colorOf`): the redundant span is
 *  filled, its original underlined in the same colour, so you can match them.
 *  Filler is a single muted advisory style. Format/whitespace stay in the list. */
function ShortenPromptView({
  content,
  findings,
  colorOf,
}: {
  content: string;
  findings: ShortenFinding[];
  colorOf: Map<ShortenFinding, string>;
}) {
  const ranges: { start: number; end: number; color: string | null; variant: Variant }[] = [];
  const add = (needle: string | null | undefined, color: string | null, variant: Variant, all: boolean) => {
    if (!needle) return;
    let i = content.indexOf(needle);
    while (i !== -1) {
      ranges.push({ start: i, end: i + needle.length, color, variant });
      if (!all) break;
      i = content.indexOf(needle, i + needle.length);
    }
  };
  for (const f of findings) {
    if (f.kind === "duplicate") {
      const c = colorOf.get(f) || null;
      add(f.text, c, "solid", true);
      add(f.counterpart, c, "dashed", false);
    } else if (f.kind === "semantic_duplicate") {
      const c = colorOf.get(f) || null;
      add(f.text, c, "solid", false);
      add(f.counterpart, c, "dashed", false);
    } else if (f.kind === "filler") {
      add(f.text, null, "filler", true);
    }
  }
  // Earliest first; on ties prefer the longer span. Skip overlaps (first wins).
  ranges.sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const chosen: typeof ranges = [];
  let lastEnd = 0;
  for (const r of ranges) if (r.start >= lastEnd) { chosen.push(r); lastEnd = r.end; }

  const runs: { text: string; color: string | null; variant: Variant | null }[] = [];
  let pos = 0;
  for (const r of chosen) {
    if (r.start > pos) runs.push({ text: content.slice(pos, r.start), color: null, variant: null });
    runs.push({ text: content.slice(r.start, r.end), color: r.color, variant: r.variant });
    pos = r.end;
  }
  if (pos < content.length) runs.push({ text: content.slice(pos), color: null, variant: null });

  return (
    <pre className="mono" style={{ margin: 0, fontSize: 12, lineHeight: 1.7, whiteSpace: "pre-wrap", wordBreak: "break-word", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 6, padding: 12, maxHeight: 360, overflow: "auto" }}>
      {runs.map((run, i) =>
        run.variant
          ? <span key={i} style={variantStyle(run.color, run.variant)}>{run.text}</span>
          : <span key={i} style={{ color: "var(--text-dim)" }}>{run.text}</span>
      )}
    </pre>
  );
}

/** Render a prompt's segments: the static prefix up to the first input is the
 *  cacheable region (green); everything from the first input on is re-billed
 *  every call (dim), with variable slots highlighted. */
function SegmentView({ analysis }: { analysis: PromptCacheAnalysis }) {
  let seenVar = false;
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
        maxHeight: 360,
        overflow: "auto",
      }}
    >
      {analysis.segments.map((seg, i) => {
        if (seg.type === "var") {
          const first = !seenVar;
          seenVar = true;
          return (
            <span key={i}>
              {first && (
                <span
                  aria-label="cacheable prefix ends here"
                  style={{
                    display: "inline-block",
                    width: 2,
                    alignSelf: "stretch",
                    marginRight: 1,
                    color: "var(--accent)",
                  }}
                >
                  ▏
                </span>
              )}
              <span
                style={{
                  background: "var(--warning-soft)",
                  color: "var(--warning)",
                  borderRadius: 3,
                  padding: "0 2px",
                  fontWeight: 600,
                }}
              >
                {seg.text}
              </span>
            </span>
          );
        }
        // static run: green (cacheable) before the first input, dim after.
        return (
          <span
            key={i}
            style={
              seenVar
                ? { color: "var(--secondary)" }
                : { background: "var(--success-soft)", color: "var(--success)", borderRadius: 2 }
            }
          >
            {seg.text}
          </span>
        );
      })}
    </pre>
  );
}

function Metric({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span className="mono" style={{ fontSize: 22, fontWeight: 600, color: color || "var(--text)", lineHeight: 1 }}>
        {value}
      </span>
      <span style={{ fontSize: 10.5, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </span>
      {sub && <span style={{ fontSize: 10.5, color: "var(--muted)" }}>{sub}</span>}
    </div>
  );
}

/** Side-by-side view: both prompts shown in full and given equal weight, each
 *  with its own unique wording highlighted (shared text is dimmed in both). */
function SideBySide({ diff }: { diff: Diff2Response }) {
  const preStyle: CSSProperties = {
    margin: 0,
    fontSize: 12,
    lineHeight: 1.65,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    background: "var(--bg)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: 10,
    maxHeight: 340,
    overflow: "auto",
    flex: 1,
  };
  const pane = (name: string, version: number, keep: "delete" | "insert") => {
    const color = keep === "delete" ? "var(--error)" : "var(--success)";
    const bg = keep === "delete" ? "var(--error-soft)" : "var(--success-soft)";
    return (
      <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 6 }}>
          <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {name}
          </span>
          <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>v{version}</span>
          <span className="mono" style={{ marginLeft: "auto", fontSize: 10, color }}>■ unique</span>
        </div>
        <pre className="mono" style={preStyle}>
          {diff.diff
            .filter((s) => s.type === "equal" || s.type === keep)
            .map((s, i) =>
              s.type === "equal" ? (
                <span key={i} style={{ color: "var(--text-dim)" }}>{s.text}</span>
              ) : (
                <span key={i} style={{ background: bg, color, borderRadius: 2 }}>{s.text}</span>
              )
            )}
        </pre>
      </div>
    );
  };
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
      {pane(diff.a.name, diff.a.latest_version, "delete")}
      {pane(diff.b.name, diff.b.latest_version, "insert")}
    </div>
  );
}

const SectionLabel = ({ children }: { children: ReactNode }) => (
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
    {children}
  </div>
);

export default function Duplicates() {
  const navigate = useNavigate();

  // ---- per-prompt cache analysis (primary) ----
  const [rows, setRows] = useState<CacheAnalysisRow[] | null>(null);
  const [rowsErr, setRowsErr] = useState<string | null>(null);
  const [selName, setSelName] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<PromptCacheAnalysis | null>(null);
  const [anaLoading, setAnaLoading] = useState(false);

  // ---- consolidation ----
  const [dups, setDups] = useState<NearDuplicates | null>(null);
  const [pairSel, setPairSel] = useState<number | null>(null);
  const [diff, setDiff] = useState<Diff2Response | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyMsg, setApplyMsg] = useState<string | null>(null);

  const [cms, setCms] = useState<boolean>(false);
  const [mode, setMode] = useState<Mode>("reorder");

  // ---- shorten ----
  const [shortenRows, setShortenRows] = useState<ShortenRow[] | null>(null);
  const [selShorten, setSelShorten] = useState<string | null>(null);
  const [shorten, setShorten] = useState<PromptShortenAnalysis | null>(null);
  const [shortenLoading, setShortenLoading] = useState(false);

  useEffect(() => {
    getCacheAnalysisList()
      .then((d) => setRows(d.prompts))
      .catch((e) => setRowsErr(String(e)));
    getNearDuplicates().then(setDups).catch(() => setDups({ mode: "none", threshold: 0.85, pairs: [] }));
    getShortenAnalysisList().then((d) => setShortenRows(d.prompts)).catch(() => setShortenRows([]));
    getConfig().then((c) => setCms(!!c.cms_enabled)).catch(() => {});
  }, []);

  const selectShorten = (name: string) => {
    setSelShorten(name);
    setShorten(null);
    setShortenLoading(true);
    getPromptShortenAnalysis(name)
      .then(setShorten)
      .catch(() => setShorten(null))
      .finally(() => setShortenLoading(false));
  };

  const selectPrompt = (name: string) => {
    setSelName(name);
    setAnalysis(null);
    setAnaLoading(true);
    getPromptCacheAnalysis(name)
      .then(setAnalysis)
      .catch(() => setAnalysis(null))
      .finally(() => setAnaLoading(false));
  };

  const dupPairs = useMemo(
    () => (dups?.pairs ?? []).slice().sort((a, b) => pairScore(b) - pairScore(a)),
    [dups]
  );

  const selectPair = (idx: number, p: NearDuplicatePair) => {
    setPairSel(idx);
    setDiff(null);
    setApplyMsg(null);
    setDiffLoading(true);
    getPromptDiff2(p.a, p.b)
      .then(setDiff)
      .catch(() => setDiff(null))
      .finally(() => setDiffLoading(false));
  };

  // Swap locally — no refetch, so the panel keeps its height and the page
  // doesn't jump. diff2 is symmetric: flip the sides and invert insert<->delete.
  const swap = () => {
    setApplyMsg(null);
    setDiff((d) =>
      d
        ? {
            ...d,
            a: d.b,
            b: d.a,
            diff: d.diff.map((s) =>
              s.type === "insert"
                ? { ...s, type: "delete" as const }
                : s.type === "delete"
                ? { ...s, type: "insert" as const }
                : s
            ),
          }
        : d
    );
  };

  const apply = () => {
    if (!diff || !cms) return;
    setApplying(true);
    setApplyMsg(null);
    // Consolidate: the right-hand prompt adopts the left-hand one's wording as a
    // new version. ↔ chooses which side is kept.
    savePromptContent(diff.b.name, diff.a.content)
      .then((r) => setApplyMsg(`✓ ${diff.b.name} updated to v${r.version} — now matches ${diff.a.name}.`))
      .catch((e) => setApplyMsg(`✗ ${String(e)}`))
      .finally(() => setApplying(false));
  };

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · cache optimization"
        title="Cache optimization"
        description="Prompt-prefix caching reuses the request up to the first changing input — so keep static instructions first and push {{inputs}} to the end."
      />

      <ModeToggle mode={mode} setMode={setMode} />

      {/* ================= REORDER: per-prompt input placement ================= */}
      {mode === "reorder" && (
      <div style={{ display: "grid", gridTemplateColumns: "minmax(260px, 0.7fr) minmax(0, 1.6fr)", gap: 14 }}>
        {/* list */}
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
            Prompts by cache opportunity
          </div>

          {!rows && !rowsErr && (
            <div style={{ padding: 14 }}>
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="skeleton" style={{ height: 34, borderRadius: 6, marginBottom: 8 }} />
              ))}
            </div>
          )}
          {rowsErr && (
            <div style={{ padding: 16, fontSize: 12, color: "var(--error)", fontFamily: "var(--font-mono)" }}>{rowsErr}</div>
          )}
          {rows && rows.length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
              No prompts recorded yet.
            </div>
          )}

          {rows?.map((r) => {
            const active = selName === r.name;
            const tone = recTone(r.recommendation);
            const badge =
              r.recommendation === "move_inputs_to_end"
                ? `reorder +${tok(r.reorder_gain_tokens)}`
                : REC_LABEL[r.recommendation];
            return (
              <button
                key={r.name}
                onClick={() => selectPrompt(r.name)}
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
                <span
                  className="mono"
                  style={{ flex: 1, minWidth: 0, fontSize: 12, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  {r.name}
                </span>
                <span
                  className="mono"
                  style={{ flexShrink: 0, fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 4, background: tone.bg, color: tone.fg }}
                >
                  {badge}
                </span>
              </button>
            );
          })}
        </div>

        {/* detail */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          {selName == null && (
            <div
              className="card"
              style={{ padding: 40, textAlign: "center", color: "var(--muted)", border: "1px dashed var(--border-strong)", background: "transparent", fontSize: 12.5 }}
            >
              Select a prompt to see its cacheable prefix and whether reordering its inputs would help.
            </div>
          )}
          {selName != null && anaLoading && (
            <div className="card" style={{ padding: 14 }}>
              <div className="skeleton" style={{ height: 12, width: "60%", borderRadius: 3, marginBottom: 10 }} />
              <div className="skeleton" style={{ height: 140, borderRadius: 6 }} />
            </div>
          )}
          {analysis && !anaLoading && (
            <>
              <div className="card enter" style={{ padding: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10, gap: 8, flexWrap: "wrap" }}>
                  <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>
                    {analysis.name}
                    <span style={{ color: "var(--muted)", fontWeight: 400 }}> v{analysis.version}</span>
                  </span>
                  <div style={{ display: "flex", gap: 12, fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
                    <span><span style={{ color: "var(--success)" }}>■</span> cacheable prefix</span>
                    <span><span style={{ color: "var(--secondary)" }}>■</span> re-billed each call</span>
                    <span><span style={{ color: "var(--warning)" }}>■</span> input</span>
                  </div>
                </div>
                <SegmentView analysis={analysis} />
                <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn" onClick={() => navigate(`/playground?prompt=${encodeURIComponent(analysis.name)}`)}>
                    Test in Playground
                  </button>
                  <button className="btn" onClick={() => navigate(`/prompts/${encodeURIComponent(analysis.name)}`)}>
                    Open prompt
                  </button>
                </div>
              </div>

              <div className="card enter" style={{ padding: 14 }}>
                <SectionLabel>Cache readiness</SectionLabel>
                <div style={{ display: "flex", gap: 26, flexWrap: "wrap", marginBottom: 14 }}>
                  <Metric label="cacheable now" value={tok(analysis.cacheable_prefix_tokens)} sub="tokens" color="var(--success)" />
                  <Metric label="if inputs last" value={tok(analysis.static_total_tokens)} sub="tokens" color="var(--accent)" />
                  <Metric
                    label="reorder gain"
                    value={analysis.reorder_gain_tokens > 0 ? "+" + tok(analysis.reorder_gain_tokens) : "—"}
                    sub="tokens / call"
                    color={analysis.reorder_gain_tokens > 0 ? "var(--warning)" : "var(--muted)"}
                  />
                  <Metric
                    label="request size"
                    value={"~" + tok(analysis.threshold_tokens)}
                    sub={analysis.meets_threshold ? `≥ ${tok(analysis.min_cache_tokens)} floor ✓` : `< ${tok(analysis.min_cache_tokens)} floor`}
                    color={analysis.meets_threshold ? "var(--text)" : "var(--muted)"}
                  />
                </div>
                <div
                  style={{
                    display: "flex",
                    gap: 8,
                    alignItems: "flex-start",
                    fontSize: 12.5,
                    lineHeight: 1.6,
                    color: "var(--text-dim)",
                    padding: "10px 12px",
                    borderRadius: 6,
                    background: recTone(analysis.recommendation).bg,
                  }}
                >
                  <span className="mono" style={{ flexShrink: 0, fontWeight: 700, color: recTone(analysis.recommendation).fg }}>
                    {REC_LABEL[analysis.recommendation]}
                  </span>
                  <span>{analysis.rationale}</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      )}

      {/* ================= CONSOLIDATE: near-duplicate forks ================= */}
      {mode === "consolidate" && (
      <div>
        <p style={{ fontSize: 12.5, color: "var(--muted)", margin: "0 0 14px", lineHeight: 1.6 }}>
          Two prompts that drifted from a common base — align their wording so the shared instruction block is cached once instead of paid for twice.
        </p>

        {dupPairs.length === 0 ? (
          <div className="card" style={{ padding: 20, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
            No near-duplicate prompts found — your registry is lean.
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 0.6fr) minmax(0, 1.6fr)", gap: 14 }}>
            <div className="card" style={{ padding: 0, overflow: "hidden", alignSelf: "start" }}>
              {dupPairs.map((p, i) => {
                const active = pairSel === i;
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
                      <div className="mono" style={{ fontSize: 12, color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.a}</div>
                      <div className="mono" style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.b}</div>
                    </div>
                    <span className="mono" style={{ flexShrink: 0, fontSize: 11, fontWeight: 700, padding: "2px 7px", borderRadius: 4, background: "var(--warning-soft)", color: "var(--warning)" }}>
                      {Math.round(pairScore(p) * 100)}%
                    </span>
                  </button>
                );
              })}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
              {pairSel == null && (
                <div className="card" style={{ padding: 32, textAlign: "center", color: "var(--muted)", border: "1px dashed var(--border-strong)", background: "transparent", fontSize: 12.5 }}>
                  Select a pair to diff the two forks.
                </div>
              )}
              {pairSel != null && diffLoading && (
                <div className="card" style={{ padding: 14 }}>
                  <div className="skeleton" style={{ height: 120, borderRadius: 6 }} />
                </div>
              )}
              {diff && !diffLoading && (
                <div className="card enter" style={{ padding: 14 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10, gap: 8 }}>
                    <span className="mono" style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      Side-by-side · {Math.round(diff.shared_prefix_ratio * 100)}% shared prefix
                    </span>
                    <button
                      onClick={swap}
                      title="Swap the two sides"
                      className="mono"
                      style={{ border: "1px solid var(--border)", background: "var(--bg-elev)", color: "var(--secondary)", borderRadius: 6, cursor: "pointer", padding: "3px 10px", fontSize: 11.5 }}
                    >
                      ↔ swap sides
                    </button>
                  </div>
                  <SideBySide diff={diff} />

                  {/* Apply — CMS-gated */}
                  <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <button
                      className="btn"
                      onClick={apply}
                      disabled={!cms || applying}
                      title={cms ? `Update ${diff.b.name} to match ${diff.a.name} (saves a new version)` : "Enable the prompt CMS to apply changes"}
                      style={{
                        borderColor: cms ? "var(--accent)" : "var(--border)",
                        color: cms ? "var(--accent)" : "var(--muted)",
                        cursor: cms ? "pointer" : "not-allowed",
                        opacity: cms ? 1 : 0.6,
                      }}
                    >
                      {applying ? "Applying…" : `Apply → update ${diff.b.name}`}
                    </button>
                    {!cms && (
                      <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
                        Prompt CMS is off — set <span className="mono" style={{ color: "var(--secondary)" }}>[dashboard] cms = true</span> to edit prompts here.
                      </span>
                    )}
                    {applyMsg && (
                      <span style={{ fontSize: 11.5, color: applyMsg.startsWith("✓") ? "var(--success)" : "var(--error)" }}>{applyMsg}</span>
                    )}
                  </div>
                  <p style={{ margin: "10px 0 0", fontSize: 11.5, color: "var(--muted)", lineHeight: 1.6 }}>
                    They share {Math.round(diff.shared_prefix_ratio * 100)}% of their wording. Consolidate onto one name so the
                    shared prefix is cached once:{" "}
                    <span className="mono" style={{ color: "var(--text)" }}>{diff.b.name}</span> adopts{" "}
                    <span className="mono" style={{ color: "var(--text)" }}>{diff.a.name}</span>'s wording, then repoint any{" "}
                    <span className="mono">track("{diff.b.name}")</span> call-sites and re-run affected suites. Use ↔ to flip which side is kept.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      )}

      {mode === "shorten" && (
      <div>
        <p style={{ fontSize: 12.5, color: "var(--muted)", margin: "0 0 14px", lineHeight: 1.6 }}>
          Flags redundant and filler wording in each prompt's static text and measures the input tokens it costs — you edit the prompt to apply. Nothing is rewritten automatically.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(260px, 0.7fr) minmax(0, 1.6fr)", gap: 14 }}>
          {/* list */}
          <div className="card" style={{ padding: 0, overflow: "hidden", alignSelf: "start" }}>
            <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", background: "var(--bg-elev)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
              Prompts by potential savings
            </div>
            {!shortenRows && (
              <div style={{ padding: 14 }}>
                {[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 34, borderRadius: 6, marginBottom: 8 }} />)}
              </div>
            )}
            {shortenRows && shortenRows.length === 0 && (
              <div style={{ padding: 24, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No prompts recorded yet.</div>
            )}
            {shortenRows?.map((r) => {
              const active = selShorten === r.name;
              const clean = r.est_tokens_saved <= 0;
              return (
                <button
                  key={r.name}
                  onClick={() => selectShorten(r.name)}
                  style={{ width: "100%", textAlign: "left", display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", border: "none", borderBottom: "1px solid var(--border)", borderLeft: "2px solid " + (active ? "var(--accent)" : "transparent"), background: active ? "var(--surface)" : "transparent", cursor: "pointer" }}
                >
                  <span className="mono" style={{ flex: 1, minWidth: 0, fontSize: 12, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</span>
                  <span className="mono" style={{ flexShrink: 0, fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 4, background: clean ? "var(--success-soft)" : "var(--warning-soft)", color: clean ? "var(--success)" : "var(--warning)" }}>
                    {clean ? "clean" : `-${tok(r.est_tokens_saved)}`}
                  </span>
                </button>
              );
            })}
          </div>

          {/* detail */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
            {selShorten == null && (
              <div className="card" style={{ padding: 40, textAlign: "center", color: "var(--muted)", border: "1px dashed var(--border-strong)", background: "transparent", fontSize: 12.5 }}>
                Select a prompt to see redundant or filler wording and how many tokens it costs.
              </div>
            )}
            {selShorten != null && shortenLoading && (
              <div className="card" style={{ padding: 14 }}><div className="skeleton" style={{ height: 140, borderRadius: 6 }} /></div>
            )}
            {shorten && !shortenLoading && ((a: PromptShortenAnalysis) => {
              // Drop zero-token findings (e.g. a stray blank-line hit worth nothing).
              const findings = a.findings.filter((f) => f.tokens > 0);
              const redundant = findings.filter((f) => f.kind !== "filler");
              const filler = findings.filter((f) => f.kind === "filler");
              const redTokens = redundant.reduce((s, f) => s + f.tokens, 0);
              const fillTokens = filler.reduce((s, f) => s + f.tokens, 0);
              // Assign each duplicate/semantic pair its own colour, shared by the
              // inline highlight and the list row so they read as the same pair.
              const colorOf = new Map<ShortenFinding, string>();
              let ci = 0;
              for (const f of findings) {
                if (f.kind === "duplicate" || f.kind === "semantic_duplicate") {
                  colorOf.set(f, PAIR_COLORS[ci % PAIR_COLORS.length]);
                  ci++;
                }
              }
              const row = (f: ShortenFinding, i: number) => {
                const pc = colorOf.get(f);
                const badge = pc || SHORTEN_KIND[f.kind].color;
                return (
                  <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "9px 11px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)" }}>
                    <span className="mono" style={{ flexShrink: 0, fontSize: 9.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", padding: "2px 6px", borderRadius: 3, color: badge, background: "color-mix(in oklch, " + badge + " 16%, transparent)" }}>{SHORTEN_KIND[f.kind].label}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 3 }}>
                        {f.message}
                        {f.similarity != null && <span style={{ color: "var(--muted)" }}> ({Math.round(f.similarity * 100)}% similar)</span>}
                      </div>
                      {f.kind !== "whitespace" && f.text.trim() && (
                        <div className="mono" style={{ fontSize: 11.5, color: "var(--secondary)", whiteSpace: "pre-wrap", wordBreak: "break-word", background: "var(--bg-elev)", borderRadius: 4, padding: "4px 7px" }}>
                          {f.text.length > 160 ? f.text.slice(0, 160) + "…" : f.text}
                        </div>
                      )}
                    </div>
                    <span className="mono" style={{ flexShrink: 0, fontSize: 11, fontWeight: 700, color: f.kind === "filler" ? "var(--muted)" : badge }}>-{tok(f.tokens)}</span>
                  </div>
                );
              };
              return (
                <div className="card enter" style={{ padding: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14, gap: 8, flexWrap: "wrap" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>
                        {a.name}<span style={{ color: "var(--muted)", fontWeight: 400 }}> v{a.version}</span>
                      </span>
                      <button className="btn" style={{ padding: "3px 10px", fontSize: 11.5 }} onClick={() => navigate(`/prompts/${encodeURIComponent(a.name)}`)}>Open to edit</button>
                    </div>
                    <div style={{ display: "flex", gap: 22 }}>
                      <Metric label="redundant" value={redTokens > 0 ? "-" + tok(redTokens) : "0"} sub="safe to cut" color={redTokens > 0 ? "var(--warning)" : "var(--muted)"} />
                      <Metric label="filler" value={fillTokens > 0 ? "-" + tok(fillTokens) : "0"} sub="optional" color="var(--muted)" />
                      <Metric label="static size" value={"~" + tok(a.total_tokens)} sub="tokens" />
                    </div>
                  </div>

                  {findings.length === 0 ? (
                    <div style={{ padding: "16px 12px", borderRadius: 6, background: "var(--success-soft)", color: "var(--success)", fontSize: 12.5 }}>
                      No redundant or filler wording found — this prompt is already tight.
                    </div>
                  ) : (
                    <>
                      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--muted)", marginBottom: 8 }}>
                        <span>each <span style={{ background: `color-mix(in oklch, ${PAIR_COLORS[0]} 24%, transparent)`, color: PAIR_COLORS[0], borderRadius: 2, padding: "0 3px" }}>colour</span> = a redundant span + its <span style={{ textDecoration: "underline dashed", textDecorationColor: PAIR_COLORS[0] }}>original</span></span>
                        <span><span style={{ textDecoration: "underline dashed", textDecorationColor: "var(--muted)", fontStyle: "italic", color: "var(--muted)" }}>filler</span> (optional)</span>
                      </div>
                      <ShortenPromptView content={a.content} findings={findings} colorOf={colorOf} />

                      {redundant.length > 0 && (
                        <div style={{ marginTop: 14 }}>
                          <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)", marginBottom: 8 }}>Redundant · safe to cut</div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{redundant.map(row)}</div>
                        </div>
                      )}
                      {filler.length > 0 && (
                        <div style={{ marginTop: 14 }}>
                          <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)", marginBottom: 4 }}>Filler · optional</div>
                          <p style={{ margin: "0 0 8px", fontSize: 11.5, color: "var(--muted)", lineHeight: 1.5 }}>
                            Phrases models usually ignore — but some prompts rely on this emphasis, so review before removing.
                          </p>
                          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{filler.map(row)}</div>
                        </div>
                      )}
                    </>
                  )}

                  {!a.semantic_available && (
                    <p style={{ margin: "12px 0 0", fontSize: 11.5, color: "var(--muted)", lineHeight: 1.6 }}>
                      Install <span className="mono" style={{ color: "var(--secondary)" }}>promptry[semantic]</span> to also catch semantically-redundant wording (not just literal repeats).
                    </p>
                  )}
                </div>
              );
            })(shorten)}
          </div>
        </div>
      </div>
      )}
    </div>
  );
}
