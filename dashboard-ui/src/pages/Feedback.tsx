import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { KPI, PageHeader } from "../components/ui";
import { pct, relTime } from "../utils";
import { listFeedback, getFeedbackStats } from "../api/client";
import { useCached } from "../lib/cache";
import type { FeedbackRow, FeedbackStats } from "../api/types";

const PAGE = 40;
const ratingColor = (r: number | null) =>
  r == null ? "var(--border-strong)" : r >= 0.7 ? "var(--success)" : r <= 0.4 ? "var(--error)" : "var(--warning)";
const ratingLabel = (r: number | null) => (r == null ? "—" : r >= 0.7 ? "positive" : r <= 0.4 ? "negative" : "mixed");

type Tab = "all" | "flagged" | "comments";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function Feedback() {
  const navigate = useNavigate();
  const { data: stats } = useCached<FeedbackStats>("feedback-stats", () => getFeedbackStats(30));
  const [tab, setTab] = useState<Tab>("all");
  const [prompt, setPrompt] = useState<string | null>(null);
  const [promptQuery, setPromptQuery] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 300);

  const [rows, setRows] = useState<FeedbackRow[]>([]);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(true);
  const sentinel = useRef<HTMLDivElement>(null);
  const scrollBox = useRef<HTMLDivElement>(null);

  const params = useMemo(
    () => ({
      days: 30,
      name: prompt || undefined,
      q: q || undefined,
      ...(tab === "flagged" ? { minRating: 0.4 } : tab === "comments" ? { onlyComments: true } : {}),
    }),
    [tab, prompt, q]
  );

  // reset + first page whenever the filter set changes
  useEffect(() => {
    let cancelled = false;
    setRows([]); setDone(false); setLoading(true);
    listFeedback({ ...params, limit: PAGE, offset: 0 })
      .then((r) => { if (!cancelled) { setRows(r.feedback); setDone(r.feedback.length < PAGE); setLoading(false); } })
      .catch(() => { if (!cancelled) { setRows([]); setDone(true); setLoading(false); } });
    return () => { cancelled = true; };
  }, [params]);

  const loadMore = useCallback(() => {
    if (loading || done) return;
    setLoading(true);
    listFeedback({ ...params, limit: PAGE, offset: rows.length })
      .then((r) => { setRows((prev) => [...prev, ...r.feedback]); setDone(r.feedback.length < PAGE); setLoading(false); })
      .catch(() => { setDone(true); setLoading(false); });
  }, [params, rows.length, loading, done]);

  // infinite scroll inside the stream's own scroll box: load the next page when
  // the sentinel nears the bottom of the box (root = the scroll container).
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) loadMore(); }, { root: scrollBox.current, rootMargin: "250px" });
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore]);

  const maxPrompt = Math.max(1, ...(stats?.by_prompt ?? []).map((p) => p.count));
  const promptRows = (stats?.by_prompt ?? []).filter((p) => p.name.toLowerCase().includes(promptQuery.trim().toLowerCase()));

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · feedback"
        title="Feedback"
        description="Every end-user rating and comment in one place — POST to /api/feedback with the request_id from track_invocation."
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 18 }}>
        <KPI label="Satisfaction" value={stats?.positive_rate != null ? pct(stats.positive_rate, 0) : "—"} accent="var(--success)" sub={stats ? `${stats.positive}/${stats.rated} rated positive` : ""} spark={stats?.sparkline?.length ? stats.sparkline : undefined} />
        <KPI label="Responses" value={stats ? stats.total.toLocaleString() : "—"} accent="var(--text)" sub={stats ? `${stats.rated} rated · ${stats.days}d` : ""} />
        <KPI label="Flagged" value={stats ? stats.negative : "—"} accent={stats && stats.negative ? "var(--error)" : "var(--text)"} sub="rating ≤ 0.4" />
        <KPI label="With a note" value={stats ? stats.with_comments : "—"} accent="var(--accent)" sub="free-text comments" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.9fr", gap: 16 }}>
        {/* satisfaction by prompt — click a row to filter the stream */}
        <div className="card" style={{ overflow: "hidden", alignSelf: "start" }}>
          <div style={{ padding: "13px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>By prompt</span>
            <span style={{ fontSize: 10.5, color: "var(--muted)" }}>click to filter</span>
          </div>
          {!stats || stats.by_prompt.length === 0 ? (
            <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>No feedback yet.</div>
          ) : (
            <>
              {stats.by_prompt.length > 8 && (
                <div style={{ padding: "9px 14px", borderBottom: "1px solid var(--border)" }}>
                  <input
                    value={promptQuery}
                    onChange={(e) => setPromptQuery(e.target.value)}
                    placeholder={`Search ${stats.by_prompt.length} prompts…`}
                    style={{ width: "100%", boxSizing: "border-box", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 6, padding: "6px 10px", color: "var(--text)", fontSize: 12, outline: "none" }}
                  />
                </div>
              )}
              <div style={{ padding: "12px 16px", maxHeight: 460, overflowY: "auto" }}>
              {promptRows.length === 0 && (
                <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: "12px 0" }}>No prompts match “{promptQuery}”.</div>
              )}
              {promptRows.map((p) => {
                const active = prompt === p.name;
                return (
                  <div
                    key={p.name}
                    onClick={() => setPrompt(active ? null : p.name)}
                    style={{ marginBottom: 10, cursor: "pointer", padding: "6px 8px", margin: "0 -8px 4px", borderRadius: 6, background: active ? "var(--bg-elev)" : "transparent" }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                      <span className="mono" style={{ fontSize: 12.5, color: active ? "var(--accent)" : "var(--text)" }}>{p.name}</span>
                      <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: ratingColor(p.avg_rating) }}>{pct(p.avg_rating, 0)}</span>
                    </div>
                    <div style={{ height: 5, background: "var(--bg)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                      <div style={{ width: (p.count / maxPrompt) * 100 + "%", height: "100%", background: ratingColor(p.avg_rating), opacity: 0.85 }} />
                    </div>
                    <div style={{ fontSize: 10.5, color: "var(--secondary)", marginTop: 3 }}>
                      {p.count} rating{p.count !== 1 ? "s" : ""}{p.negative ? ` · ${p.negative} flagged` : ""}
                    </div>
                  </div>
                );
              })}
              </div>
            </>
          )}
        </div>

        {/* feedback stream — searchable, lazy-loaded */}
        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
              Feedback stream
              {prompt && (
                <span className="mono" onClick={() => setPrompt(null)} title="clear filter"
                  style={{ cursor: "pointer", fontSize: 11, color: "var(--accent)", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 5, padding: "2px 7px", fontWeight: 400 }}>
                  {prompt} ✕
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {([["all", "All"], ["flagged", "Flagged"], ["comments", "With notes"]] as [Tab, string][]).map(([t, label]) => (
                <button key={t} className="btn" onClick={() => setTab(t)} style={tab === t ? { background: "var(--accent)", color: "var(--bg)", borderColor: "var(--accent)" } : undefined}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div style={{ padding: "9px 14px", borderBottom: "1px solid var(--border)" }}>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search comments and prompts…"
              style={{ width: "100%", boxSizing: "border-box", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 6, padding: "7px 11px", color: "var(--text)", fontSize: 12.5, outline: "none" }}
            />
          </div>

          {rows.length === 0 && !loading ? (
            <div style={{ padding: 30, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
              {q || prompt || tab !== "all" ? "No feedback matches these filters." : "No feedback in the last 30 days."}
            </div>
          ) : (
            <div ref={scrollBox} style={{ maxHeight: 560, overflowY: "auto" }}>
              <table className="pr" style={{ tableLayout: "fixed", width: "100%" }}>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} onClick={() => r.invocation_id && navigate(`/invocations/${r.invocation_id}`, { state: { from: [{ label: "feedback", to: "/feedback" }] } })} style={{ cursor: r.invocation_id ? "pointer" : "default" }}>
                      <td style={{ width: 30, paddingRight: 0 }}>
                        <span title={ratingLabel(r.rating)} style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: ratingColor(r.rating) }} />
                      </td>
                      <td style={{ width: "26%" }}>
                        <div className="mono" style={{ fontSize: 12.5, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.prompt_name || "—"}</div>
                        <div style={{ fontSize: 10.5, color: "var(--secondary)", marginTop: 2 }}>{r.model || "unknown model"}</div>
                      </td>
                      <td style={{ color: r.comment ? "var(--text)" : "var(--muted)", fontSize: 12.5, lineHeight: 1.45 }}>
                        {r.comment?.trim() || <span style={{ fontStyle: "italic" }}>no comment</span>}
                        {r.source && <span className="mono" style={{ marginLeft: 8, fontSize: 10, color: "var(--secondary)" }}>· {r.source}</span>}
                      </td>
                      <td className="mono r" style={{ width: 64, color: "var(--secondary)", fontSize: 11, whiteSpace: "nowrap" }}>{relTime(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!done && <div ref={sentinel} style={{ padding: 14, textAlign: "center", color: "var(--muted)", fontSize: 11.5 }}>{loading ? "Loading…" : "Scroll for more"}</div>}
              {done && rows.length > 0 && <div style={{ padding: 12, textAlign: "center", color: "var(--secondary)", fontSize: 11 }}>{rows.length} shown · end of feedback</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
