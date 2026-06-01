import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { relTime } from "../utils";
import { listInvocations, getInvocation } from "../api/client";
import { prefetch } from "../lib/cache";
import type { InvocationRow } from "../api/types";

const PAGE = 50;
const fmtCost = (n: number | null) => (n == null ? "—" : "$" + n.toFixed(n < 0.01 ? 5 : 4));
const fmtMs = (n: number | null) => (n == null ? "—" : n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms");

type SortKey = "created_at" | "cost" | "latency_ms" | "tokens_in" | "tokens_out";

function ratingChip(r: number | null) {
  if (r == null) return <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>;
  const color = r >= 0.66 || r >= 4 ? "var(--success)" : r >= 0.33 || r >= 2.5 ? "var(--warning)" : "var(--error)";
  return <span className="chip mono" style={{ fontSize: 10, color, borderColor: color }}>★ {r <= 1 ? (r * 5).toFixed(1) : r.toFixed(0)}</span>;
}

/** Reusable invocation list. Sorting + paging are server-side: column clicks
 *  set sort/direction and refetch; scrolling lazy-loads the next page. Clicking
 *  a row opens the shared /invocations/:id page. */
export function InvocationsPanel({
  name,
  order = "recent",
  days = 30,
  emptyHint,
  crumbs,
}: {
  name?: string;
  order?: "recent" | "cost";
  days?: number;
  emptyHint?: string;
  crumbs?: { label: string; to?: string }[];
}) {
  const nav = useNavigate();
  const [sortKey, setSortKey] = useState<SortKey>(order === "cost" ? "cost" : "created_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [rows, setRows] = useState<InvocationRow[]>([]);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(true);
  const sentinel = useRef<HTMLDivElement>(null);
  const scrollBox = useRef<HTMLDivElement>(null);

  // reset + first page whenever the query (name/window/sort) changes
  useEffect(() => {
    let cancelled = false;
    setRows([]); setDone(false); setLoading(true);
    listInvocations({ name, days, limit: PAGE, offset: 0, sort: sortKey, direction: sortDir })
      .then((r) => { if (!cancelled) { setRows(r.invocations); setDone(r.invocations.length < PAGE); setLoading(false); } })
      .catch(() => { if (!cancelled) { setRows([]); setDone(true); setLoading(false); } });
    return () => { cancelled = true; };
  }, [name, days, sortKey, sortDir]);

  const loadMore = useCallback(() => {
    if (loading || done) return;
    setLoading(true);
    listInvocations({ name, days, limit: PAGE, offset: rows.length, sort: sortKey, direction: sortDir })
      .then((r) => { setRows((prev) => [...prev, ...r.invocations]); setDone(r.invocations.length < PAGE); setLoading(false); })
      .catch(() => { setDone(true); setLoading(false); });
  }, [name, days, sortKey, sortDir, rows.length, loading, done]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) loadMore(); }, { root: scrollBox.current, rootMargin: "250px" });
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore]);

  function toggleSort(k: SortKey) {
    if (k === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(k); setSortDir("desc"); }
  }

  const Th = ({ k, label, align = "right" }: { k: SortKey; label: string; align?: "left" | "right" }) => (
    <th onClick={() => toggleSort(k)} className={align === "right" ? "r" : ""} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}>
      {label}
      <span style={{ display: "inline-block", width: 0, overflow: "visible", paddingLeft: 4, color: sortKey === k ? "var(--accent)" : "transparent", fontSize: 9 }}>
        {sortKey === k ? (sortDir === "desc" ? "▼" : "▲") : "▼"}
      </span>
    </th>
  );

  return (
    <div className="card" style={{ overflow: "hidden", height: "fit-content" }}>
      <div ref={scrollBox} style={{ maxHeight: 540, overflowY: "auto" }}>
      <table className="pr">
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Call</th>
            <Th k="tokens_in" label="Tok in" />
            <Th k="tokens_out" label="Tok out" />
            <Th k="cost" label="Cost" />
            <Th k="latency_ms" label="Latency" />
            <th style={{ textAlign: "left" }}>Rating</th>
            <Th k="created_at" label="When" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              onMouseEnter={() => prefetch(`invocation:${r.id}`, () => getInvocation(r.id))}
              onClick={() => { prefetch(`invocation:${r.id}`, () => getInvocation(r.id)); nav(`/invocations/${r.id}`, { state: { from: crumbs } }); }}
              style={{ cursor: "pointer" }}
            >
              <td style={{ textAlign: "left" }}>
                <div style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="mono" style={{ color: "var(--text-dim)" }}>#{r.id}</span>
                  {r.model && <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{r.model}</span>}
                  {r.has_capture && <span style={{ color: "var(--accent)", fontSize: 9 }} title="captured">●</span>}
                </div>
                {r.output_preview && (
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 320 }}>
                    {r.output_preview}
                  </div>
                )}
              </td>
              <td className="r mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{r.tokens_in ?? "—"}</td>
              <td className="r mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{r.tokens_out ?? "—"}</td>
              <td className="r mono" style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: sortKey === "cost" ? 600 : 400 }}>{fmtCost(r.cost)}</td>
              <td className="r mono" style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{fmtMs(r.latency_ms)}</td>
              <td style={{ textAlign: "left" }}>{ratingChip(r.rating)}</td>
              <td className="r mono" style={{ fontSize: 11, color: "var(--secondary)", whiteSpace: "nowrap" }}>{relTime(r.created_at)}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={7} style={{ textAlign: "center", padding: 36, color: "var(--muted)", fontSize: 12.5 }}>
              {loading ? "Loading…" : emptyHint || "No invocations in this window."}
            </td></tr>
          )}
        </tbody>
      </table>
      {!done && rows.length > 0 && <div ref={sentinel} style={{ padding: 12, textAlign: "center", color: "var(--muted)", fontSize: 11.5 }}>{loading ? "Loading…" : "Scroll for more"}</div>}
      {done && rows.length > 0 && <div style={{ padding: 10, textAlign: "center", color: "var(--secondary)", fontSize: 11 }}>{rows.length} shown · end of list</div>}
      </div>
    </div>
  );
}
