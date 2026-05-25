import { useEffect, useState } from "react";
import { BarChart, KPI, PageHeader, Select } from "../components/ui";
import { formatTokens, pct, usd } from "../utils";
import { getCostData } from "../api/client";
import type { CostResponse } from "../api/types";

export default function Cost() {
  const [days, setDays] = useState(14);
  const [data, setData] = useState<CostResponse | null>(null);

  useEffect(() => {
    getCostData(days)
      .then(setData)
      .catch(() => setData(null));
  }, [days]);

  if (!data) {
    return (
      <div>
        <PageHeader eyebrow="~/promptry · cost" title="Cost & Tokens" description="Loading…" />
      </div>
    );
  }

  const s = data.summary;
  const byDate = data.by_date.map((d) => ({
    ...d,
    cache_savings: 0,
  }));

  // Roll prompts up into modules (the name segment before the first dot).
  const byModule = (() => {
    const m = new Map<string, { module: string; calls: number; tokens_in: number; tokens_out: number; cost: number }>();
    for (const b of data.by_name) {
      const mod = b.name.includes(".") ? b.name.split(".")[0] : "other";
      const e = m.get(mod) || { module: mod, calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 };
      e.calls += b.calls;
      e.tokens_in += b.tokens_in;
      e.tokens_out += b.tokens_out;
      e.cost += b.cost;
      m.set(mod, e);
    }
    return [...m.values()].sort((a, b) => b.cost - a.cost);
  })();
  const moduleMaxCost = Math.max(1e-9, ...byModule.map((r) => r.cost));

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · cost"
        title="Cost & Tokens"
        description="Rolling spend across all prompts with cache credit breakdown."
        actions={
          <Select
            value={days}
            onChange={setDays}
            options={[
              { value: 7, label: "Last 7 days" },
              { value: 14, label: "Last 14 days" },
              { value: 30, label: "Last 30 days" },
            ]}
          />
        }
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <KPI
          label="Total spend"
          value={usd(s.total_cost, 2)}
          accent="var(--text)"
          sub={`${s.total_calls.toLocaleString()} calls`}
        />
        <KPI
          label="Avg $/call"
          value={"$" + (s.avg_cost ?? 0).toFixed(5)}
          accent="var(--accent)"
          sub={`${formatTokens(s.total_tokens_in)} in · ${formatTokens(s.total_tokens_out)} out`}
        />
        <KPI
          label="Cache hit rate"
          value={pct(s.cache_hit_rate ?? 0, 1)}
          accent="var(--success)"
          sub={s.cache_savings != null ? `saved ${usd(s.cache_savings, 2)}` : ""}
        />
        <KPI
          label="Tokens (in)"
          value={formatTokens(s.total_tokens_in)}
          accent="var(--text-dim)"
          sub={`${formatTokens(s.total_tokens_out)} out`}
        />
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Daily spend</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              Orange = billed spend per day.
            </div>
          </div>
          <div style={{ display: "flex", gap: 14, fontSize: 11, color: "var(--secondary)", fontFamily: "var(--font-mono)" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 10, height: 10, background: "var(--accent)", borderRadius: 2 }} />
              Billed
            </span>
          </div>
        </div>
        <BarChart
          data={byDate}
          valueKey="cost"
          labelKey="date"
          height={220}
          format={(v) => "$" + v.toFixed(2)}
        />
      </div>

      {/* By module — cost rolled up per functional module */}
      <div className="card" style={{ overflow: "hidden", marginBottom: 20 }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>By module</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
            Spend grouped by the prompt-name prefix (agent, chunking, summary…).
          </div>
        </div>
        <table className="pr">
          <thead>
            <tr>
              <th>Module</th>
              <th className="r">Calls</th>
              <th className="r">Tokens in</th>
              <th className="r">Tokens out</th>
              <th>Share</th>
              <th className="r">Cost</th>
            </tr>
          </thead>
          <tbody>
            {byModule.map((r) => (
              <tr key={r.module}>
                <td style={{ fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: 13 }}>{r.module}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{r.calls.toLocaleString()}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{formatTokens(r.tokens_in)}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{formatTokens(r.tokens_out)}</td>
                <td>
                  <div style={{ width: 90, height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                    <div style={{ width: (r.cost / moduleMaxCost) * 100 + "%", height: "100%", background: "var(--accent)", opacity: 0.85 }} />
                  </div>
                </td>
                <td className="r mono" style={{ color: "var(--accent)", fontWeight: 600 }}>${r.cost.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>By prompt</div>
        </div>
        <table className="pr">
          <thead>
            <tr>
              <th>Name</th>
              <th className="r">Calls</th>
              <th className="r">Tokens in</th>
              <th className="r">Tokens out</th>
              <th>Cache</th>
              <th>Models</th>
              <th className="r">Cost</th>
            </tr>
          </thead>
          <tbody>
            {data.by_name.map((b) => (
              <tr key={b.name}>
                <td>
                  <span style={{ fontWeight: 600 }}>{b.name}</span>
                </td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>
                  {b.calls.toLocaleString()}
                </td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>
                  {formatTokens(b.tokens_in)}
                </td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>
                  {formatTokens(b.tokens_out)}
                </td>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 130 }}>
                    <div
                      style={{
                        width: 60,
                        height: 4,
                        background: "var(--bg-elev)",
                        borderRadius: 2,
                        overflow: "hidden",
                        border: "1px solid var(--border)",
                      }}
                    >
                      <div
                        style={{
                          width: ((b.cache_hit_rate ?? 0) * 100) + "%",
                          height: "100%",
                          background: "var(--success)",
                          opacity: 0.8,
                        }}
                      />
                    </div>
                    <span className="mono" style={{ fontSize: 11.5, color: "var(--success)" }}>
                      {((b.cache_hit_rate ?? 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                </td>
                <td>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {b.models.map((m) => (
                      <span key={m} className="chip mono" style={{ fontSize: 10 }}>
                        {m}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="r mono" style={{ color: "var(--accent)", fontWeight: 600 }}>
                  ${b.cost.toFixed(2)}
                </td>
              </tr>
            ))}
            {data.by_name.length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
                  No cost data yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
