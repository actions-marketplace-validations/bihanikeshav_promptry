import { useEffect, useState } from "react";
import { getCostData } from "../api/client";
import { useCached } from "../lib/cache";
import type { SuiteSummary, CostResponse } from "../api/types";

/** High-signal status strip: today's real spend/calls/tokens from the
 *  ledger, plus eval health only when suites actually exist. */
export function ActivityFooter({ suites }: { suites: SuiteSummary[] }) {
  const { data: cost } = useCached<CostResponse>("footer-cost:1", () => getCostData(1));
  const [, force] = useState(0);
  useEffect(() => { const t = setInterval(() => force((n) => n + 1), 60000); return () => clearInterval(t); }, []);

  const s = cost?.summary;
  const regressions = suites.filter((x) => !x.passed).length;

  return (
    <div
      style={{
        borderTop: "1px solid var(--border)",
        background: "var(--bg-elev)",
        padding: "7px 24px",
        display: "flex",
        alignItems: "center",
        gap: 18,
        fontSize: 11,
        fontFamily: "var(--font-mono)",
        color: "var(--text-dim)",
      }}
    >
      <Stat label="today" value={s ? "$" + s.total_cost.toFixed(2) : "—"} />
      <Stat label="calls" value={s ? s.total_calls.toLocaleString() : "—"} />
      <Stat label="tok in/out" value={s ? `${fmt(s.total_tokens_in)}/${fmt(s.total_tokens_out)}` : "—"} />
      {suites.length > 0 && (
        <Stat
          label="evals"
          value={`${suites.length - regressions}/${suites.length} passing`}
          color={regressions > 0 ? "var(--error)" : "var(--success)"}
        />
      )}
      <span style={{ marginLeft: "auto", color: "var(--muted)" }}>promptry</span>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ color: "var(--muted)" }}>{label}</span>
      <span style={{ color: color || "var(--text)" }}>{value}</span>
    </span>
  );
}

function fmt(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}
