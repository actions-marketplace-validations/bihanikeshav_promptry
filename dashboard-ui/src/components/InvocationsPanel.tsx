import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { relTime } from "../utils";
import { listInvocations } from "../api/client";
import type { InvocationRow } from "../api/types";

const fmtCost = (n: number | null) => (n == null ? "—" : "$" + n.toFixed(n < 0.01 ? 5 : 4));
const fmtMs = (n: number | null) => (n == null ? "—" : n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms");

type SortKey = "created_at" | "cost" | "latency_ms" | "tokens_in" | "tokens_out";

function ratingChip(r: number | null) {
  if (r == null) return <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>;
  const color = r >= 0.66 || r >= 4 ? "var(--success)" : r >= 0.33 || r >= 2.5 ? "var(--warning)" : "var(--error)";
  return <span className="chip mono" style={{ fontSize: 10, color, borderColor: color }}>★ {r <= 1 ? (r * 5).toFixed(1) : r.toFixed(0)}</span>;
}

/** Reusable invocation list with sortable columns. Clicking a row opens the
 *  shared /invocations/:id page. `order` sets the default sort. */
export function InvocationsPanel({
  name,
  order = "recent",
  days = 30,
  emptyHint,
}: {
  name?: string;
  order?: "recent" | "cost";
  days?: number;
  emptyHint?: string;
}) {
  const nav = useNavigate();
  const [rows, setRows] = useState<InvocationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>(order === "cost" ? "cost" : "created_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    setLoading(true);
    listInvocations({ name, order, days, limit: 200 })
      .then((r) => setRows(r.invocations))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [name, order, days]);

  function toggleSort(k: SortKey) {
    if (k === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(k); setSortDir(k === "created_at" ? "desc" : "desc"); }
  }

  const sorted = useMemo(() => {
    const val = (r: InvocationRow): number => {
      if (sortKey === "created_at") return new Date(r.created_at).getTime();
      const v = r[sortKey];
      return v == null ? -Infinity : (v as number);
    };
    const arr = [...rows].sort((a, b) => val(a) - val(b));
    return sortDir === "desc" ? arr.reverse() : arr;
  }, [rows, sortKey, sortDir]);

  const Th = ({ k, label, align = "right" }: { k: SortKey; label: string; align?: "left" | "right" }) => (
    <th
      onClick={() => toggleSort(k)}
      className={align === "right" ? "r" : ""}
      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
    >
      {label}
      <span style={{ marginLeft: 4, color: sortKey === k ? "var(--accent)" : "transparent", fontSize: 9 }}>
        {sortKey === k ? (sortDir === "desc" ? "▼" : "▲") : "▼"}
      </span>
    </th>
  );

  return (
    <div className="card" style={{ overflow: "hidden", height: "fit-content" }}>
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
          {sorted.map((r) => (
            <tr key={r.id} onClick={() => nav(`/invocations/${r.id}`)} style={{ cursor: "pointer" }}>
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
          {sorted.length === 0 && (
            <tr><td colSpan={7} style={{ textAlign: "center", padding: 36, color: "var(--muted)", fontSize: 12.5 }}>
              {loading ? "Loading…" : emptyHint || "No invocations in this window."}
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
