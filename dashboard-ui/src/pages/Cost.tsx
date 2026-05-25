import { useEffect, useState } from "react";
import { BarChart, KPI, PageHeader, Select } from "../components/ui";
import { InvocationsPanel } from "../components/InvocationsPanel";
import { formatTokens, pct, usd } from "../utils";
import { getCostData, getCostCoverage, getPromptStats } from "../api/client";
import type { CostResponse, CostCoverage, PromptStats } from "../api/types";

type Drill = { level: "overview" } | { level: "module"; module: string } | { level: "prompt"; module: string; prompt: string };

export default function Cost() {
  const [days, setDays] = useState(14);
  const [data, setData] = useState<CostResponse | null>(null);
  const [coverage, setCoverage] = useState<CostCoverage | null>(null);
  const [drill, setDrill] = useState<Drill>({ level: "overview" });

  useEffect(() => {
    getCostData(days).then(setData).catch(() => setData(null));
    getCostCoverage(days).then(setCoverage).catch(() => setCoverage(null));
  }, [days]);

  if (!data) {
    return <div><PageHeader eyebrow="~/promptry · cost" title="Cost & Tokens" description="Loading…" /></div>;
  }

  const s = data.summary;
  const moduleOf = (n: string) => (n.includes(".") ? n.split(".")[0] : "other");

  // Module rollup.
  const modules = (() => {
    const m = new Map<string, { module: string; calls: number; tokens_in: number; tokens_out: number; cost: number }>();
    for (const b of data.by_name) {
      const mod = moduleOf(b.name);
      const e = m.get(mod) || { module: mod, calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 };
      e.calls += b.calls; e.tokens_in += b.tokens_in; e.tokens_out += b.tokens_out; e.cost += b.cost;
      m.set(mod, e);
    }
    return [...m.values()].sort((a, b) => b.cost - a.cost);
  })();
  const moduleMax = Math.max(1e-9, ...modules.map((r) => r.cost));

  const daysSelect = (
    <Select value={days} onChange={setDays} options={[
      { value: 7, label: "Last 7 days" }, { value: 14, label: "Last 14 days" }, { value: 30, label: "Last 30 days" },
    ]} />
  );

  // Breadcrumb for drilled views.
  const crumb = (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, marginBottom: 14, fontFamily: "var(--font-mono)" }}>
      <button className="lnk" onClick={() => setDrill({ level: "overview" })} style={crumbStyle(drill.level === "overview")}>cost</button>
      {drill.level !== "overview" && (<>
        <span style={{ color: "var(--muted)" }}>/</span>
        <button className="lnk" onClick={() => setDrill({ level: "module", module: drill.module })} style={crumbStyle(drill.level === "module")}>{drill.module}</button>
      </>)}
      {drill.level === "prompt" && (<>
        <span style={{ color: "var(--muted)" }}>/</span>
        <span style={{ color: "var(--text)" }}>{drill.prompt.slice(drill.prompt.indexOf(".") + 1)}</span>
      </>)}
    </div>
  );

  return (
    <div>
      <PageHeader eyebrow="~/promptry · cost" title="Cost & Tokens"
        description="Follow the money: module spend → prompt → the priciest individual calls." actions={daysSelect} />

      {drill.level !== "overview" && crumb}

      {drill.level === "overview" && (
        <OverviewLevel
          s={s} data={data} coverage={coverage} modules={modules} moduleMax={moduleMax}
          onModule={(m: string) => setDrill({ level: "module", module: m })}
        />
      )}

      {drill.level === "module" && (
        <ModuleLevel
          module={drill.module} data={data} moduleOf={moduleOf}
          onPrompt={(p: string) => setDrill({ level: "prompt", module: drill.module, prompt: p })}
        />
      )}

      {drill.level === "prompt" && <PromptCostLevel prompt={drill.prompt} days={days} />}
    </div>
  );
}

function crumbStyle(active: boolean): React.CSSProperties {
  return { background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "var(--font-mono)", fontSize: 12.5, color: active ? "var(--text)" : "var(--accent)" };
}

/* ---- overview ---- */
function OverviewLevel({ s, data, coverage, modules, moduleMax, onModule }: any) {
  const byDate = data.by_date.map((d: any) => ({ ...d, cache_savings: 0 }));
  return (
    <>
      {coverage && coverage.uncosted.length > 0 && (
        <div style={{ marginBottom: 16, padding: "11px 14px", borderRadius: 8, border: "1px solid var(--warning)", background: "color-mix(in oklch, var(--warning) 12%, transparent)", fontSize: 12.5, color: "var(--text-dim)" }}>
          <span style={{ color: "var(--warning)", fontWeight: 600 }}>{coverage.uncosted.length} model(s) with no pricing</span> — {coverage.uncosted_calls.toLocaleString()} calls counted as $0. Missing: <span className="mono">{coverage.uncosted.map((m: any) => m.model).join(", ")}</span>.
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <KPI label="Total spend" value={usd(s.total_cost, 2)} accent="var(--text)" sub={`${s.total_calls.toLocaleString()} calls`} />
        <KPI label="Avg $/call" value={"$" + (s.avg_cost ?? 0).toFixed(5)} accent="var(--accent)" sub={`${formatTokens(s.total_tokens_in)} in · ${formatTokens(s.total_tokens_out)} out`} />
        <KPI label="Cache hit rate" value={pct(s.cache_hit_rate ?? 0, 1)} accent="var(--success)" sub={s.cache_savings != null ? `saved ${usd(s.cache_savings, 2)}` : ""} />
        <KPI label="Modules" value={modules.length} accent="var(--text-dim)" sub="grouped by prefix" />
      </div>
      <div className="card" style={{ padding: 16, marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>Daily spend</div>
        <BarChart data={byDate} valueKey="cost" labelKey="date" height={200} format={(v: number) => "$" + v.toFixed(2)} />
      </div>
      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>By module</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>Click a module to drill into its prompts.</div>
        </div>
        <table className="pr">
          <thead><tr><th>Module</th><th className="r">Calls</th><th className="r">Tokens in</th><th>Share</th><th className="r">Cost</th><th className="r"></th></tr></thead>
          <tbody>
            {modules.map((r: any) => (
              <tr key={r.module} onClick={() => onModule(r.module)} style={{ cursor: "pointer" }}>
                <td style={{ fontWeight: 700, fontFamily: "var(--font-mono)", fontSize: 13 }}>{r.module}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{r.calls.toLocaleString()}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{formatTokens(r.tokens_in)}</td>
                <td><div style={{ width: 90, height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}><div style={{ width: (r.cost / moduleMax) * 100 + "%", height: "100%", background: "var(--accent)", opacity: 0.85 }} /></div></td>
                <td className="r mono" style={{ color: "var(--accent)", fontWeight: 600 }}>${r.cost.toFixed(2)}</td>
                <td className="r"><span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>›</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ---- module: its prompts ranked by spend ---- */
function ModuleLevel({ module, data, moduleOf, onPrompt }: any) {
  const prompts = data.by_name.filter((b: any) => moduleOf(b.name) === module).sort((a: any, b: any) => b.cost - a.cost);
  const max = Math.max(1e-9, ...prompts.map((p: any) => p.cost));
  const totals = prompts.reduce((acc: any, p: any) => ({ cost: acc.cost + p.cost, calls: acc.calls + p.calls }), { cost: 0, calls: 0 });
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 18 }}>
        <KPI label={`${module} spend`} value={usd(totals.cost, 2)} accent="var(--accent)" sub={`${totals.calls.toLocaleString()} calls`} />
        <KPI label="Prompts" value={prompts.length} accent="var(--text)" sub="in this module" />
        <KPI label="Avg $/call" value={"$" + (totals.calls ? totals.cost / totals.calls : 0).toFixed(5)} accent="var(--text-dim)" sub="" />
      </div>
      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", fontSize: 13, fontWeight: 600 }}>Prompts by spend</div>
        <table className="pr">
          <thead><tr><th>Prompt</th><th className="r">Calls</th><th className="r">Tokens in/out</th><th>Share</th><th className="r">Cost</th><th className="r"></th></tr></thead>
          <tbody>
            {prompts.map((p: any) => (
              <tr key={p.name} onClick={() => onPrompt(p.name)} style={{ cursor: "pointer" }}>
                <td style={{ fontWeight: 600, fontSize: 13 }}><span style={{ color: "var(--muted)" }}>{module}.</span>{p.name.slice(p.name.indexOf(".") + 1)}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{p.calls.toLocaleString()}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{formatTokens(p.tokens_in)}/{formatTokens(p.tokens_out)}</td>
                <td><div style={{ width: 90, height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}><div style={{ width: (p.cost / max) * 100 + "%", height: "100%", background: "var(--accent)", opacity: 0.85 }} /></div></td>
                <td className="r mono" style={{ color: "var(--accent)", fontWeight: 600 }}>${p.cost.toFixed(2)}</td>
                <td className="r"><span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>›</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ---- prompt: cost stats + cost-sorted invocations ---- */
function PromptCostLevel({ prompt, days }: { prompt: string; days: number }) {
  const [stats, setStats] = useState<PromptStats | null>(null);
  useEffect(() => { getPromptStats(prompt, days).then(setStats).catch(() => setStats(null)); }, [prompt, days]);
  const c = stats?.metrics.cost;
  const fmt = (n: number | undefined) => (n == null ? "—" : "$" + n.toFixed(5));
  return (
    <>
      {stats && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 18 }}>
          <KPI label="Total spend" value={c ? usd(c.sum, 2) : "—"} accent="var(--accent)" sub={`${stats.count.toLocaleString()} calls (${days}d)`} />
          <KPI label="Avg $/call" value={fmt(c?.avg)} accent="var(--text)" sub="" />
          <KPI label="p95 $/call" value={fmt(c?.p95)} accent="var(--warning)" sub="tail cost" />
          <KPI label="Max $/call" value={fmt(c?.max)} accent="var(--error)" sub="priciest single call" />
        </div>
      )}
      <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 8 }}>
        Most expensive calls first — click one to see exactly what drove the cost.
      </div>
      <InvocationsPanel name={prompt} order="cost" days={days}
        emptyHint="No invocations captured for this prompt in the window." />
    </>
  );
}
