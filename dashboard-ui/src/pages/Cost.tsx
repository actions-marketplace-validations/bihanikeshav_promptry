import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BarChart, KPI, PageHeader, Select, Breadcrumbs } from "../components/ui";
import { InvocationsPanel } from "../components/InvocationsPanel";
import { formatTokens, pct, usd } from "../utils";
import { getCostData, getCostCoverage, getPromptStats, listBudgets, createBudget, deleteBudget } from "../api/client";
import { useCached, invalidateCache } from "../lib/cache";
import type { PromptStats, BudgetStatus } from "../api/types";

export default function Cost() {
  const [days, setDays] = useState(14);
  // Cached so back/forward nav renders instantly instead of reloading.
  const { data } = useCached(`cost:${days}`, () => getCostData(days));
  const { data: coverage } = useCached(`cost-cov:${days}`, () => getCostCoverage(days));
  // Drill lives in the URL so breadcrumbs deep-link back to each level.
  const [params, setParams] = useSearchParams();
  const module = params.get("module");
  const prompt = params.get("prompt");
  const level: "overview" | "module" | "prompt" = prompt ? "prompt" : module ? "module" : "overview";

  const goModule = (m: string) => setParams({ module: m });
  const goPrompt = (m: string, p: string) => setParams({ module: m, prompt: p });

  if (!data) {
    return <div><PageHeader eyebrow="~/promptry · cost" title="Cost & Tokens" description="Loading…" /></div>;
  }

  const s = data.summary;
  const moduleOf = (n: string) => (n.includes(".") ? n.split(".")[0] : "other");
  const short = (p: string) => p.slice(p.indexOf(".") + 1);

  // Breadcrumb items reflecting the drill path (deep-linkable).
  const crumbs: { label: string; to?: string }[] = [{ label: "cost", to: "/cost" }];
  if (module) crumbs.push({ label: module, to: level === "prompt" ? `/cost?module=${encodeURIComponent(module)}` : undefined });
  if (prompt) crumbs.push({ label: short(prompt) });

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

  return (
    <div>
      {/* Breadcrumb always topmost, above the title (consistent with Prompts). */}
      {level !== "overview" && <Breadcrumbs items={crumbs} />}

      <PageHeader eyebrow="~/promptry · cost" title="Cost & Tokens"
        description="Follow the money: module spend → prompt → the priciest individual calls. Model rates from LiteLLM model_cost (MIT)." actions={daysSelect} />

      {level === "overview" && (
        <OverviewLevel s={s} data={data} coverage={coverage} modules={modules} moduleMax={moduleMax} onModule={goModule} />
      )}
      {level === "module" && module && (
        <ModuleLevel module={module} data={data} moduleOf={moduleOf} onPrompt={(p: string) => goPrompt(module, p)} />
      )}
      {level === "prompt" && prompt && module && (
        <PromptCostLevel prompt={prompt} module={module} days={days} />
      )}
    </div>
  );
}

/* ---- budgets: burn-down + add/remove ---- */
function BudgetsPanel() {
  const [budgets, setBudgets] = useState<BudgetStatus[]>([]);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ scope: "global", target: "", period: "monthly", limit_usd: "" });

  const reload = () => listBudgets().then((r) => setBudgets(r.budgets)).catch(() => setBudgets([]));
  useEffect(() => { reload(); }, []);

  async function add() {
    const lim = parseFloat(form.limit_usd);
    if (!lim || lim <= 0) return;
    await createBudget({ scope: form.scope, target: form.scope === "global" ? null : form.target.trim() || null, period: form.period, limit_usd: lim });
    invalidateCache("cost"); setForm({ scope: "global", target: "", period: "monthly", limit_usd: "" }); setAdding(false); reload();
  }
  async function remove(id: number) { await deleteBudget(id); reload(); }

  return (
    <div className="card" style={{ overflow: "hidden", marginBottom: 20 }}>
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Budgets</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>Spend caps per period — breaches highlight in red.</div>
        </div>
        <button className="btn" onClick={() => setAdding((a) => !a)}>{adding ? "Cancel" : "+ Budget"}</button>
      </div>

      {adding && (
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Select value={form.scope} onChange={(v: string) => setForm({ ...form, scope: v })} minWidth={110}
            options={[{ value: "global", label: "Global" }, { value: "module", label: "Module" }, { value: "prompt", label: "Prompt" }]} />
          {form.scope !== "global" && (
            <input className="inp" placeholder={form.scope === "module" ? "module name" : "prompt name"} value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} style={{ width: 180 }} />
          )}
          <Select value={form.period} onChange={(v: string) => setForm({ ...form, period: v })} minWidth={110}
            options={[{ value: "daily", label: "Daily" }, { value: "monthly", label: "Monthly" }]} />
          <input className="inp" placeholder="limit $" value={form.limit_usd} onChange={(e) => setForm({ ...form, limit_usd: e.target.value })} style={{ width: 90 }} />
          <button className="btn" onClick={add} style={{ background: "var(--accent)", color: "var(--bg)" }}>Add</button>
        </div>
      )}

      {budgets.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
          No budgets yet. Add one to track spend against a cap.
        </div>
      ) : (
        <div style={{ padding: "8px 16px 12px" }}>
          {budgets.map((b) => {
            const label = b.scope === "global" ? "All prompts" : `${b.scope}: ${b.target}`;
            const barColor = b.breached ? "var(--error)" : b.pct >= 80 ? "var(--warning)" : "var(--accent)";
            return (
              <div key={b.id} style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: 12.5 }}>
                  <span><span style={{ fontWeight: 600 }}>{label}</span> <span style={{ color: "var(--muted)", fontSize: 11 }}>· {b.period}</span></span>
                  <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span className="mono" style={{ color: b.breached ? "var(--error)" : "var(--text-dim)" }}>
                      ${b.spend.toFixed(2)} / ${b.limit_usd.toFixed(2)} ({b.pct.toFixed(0)}%)
                    </span>
                    <span onClick={() => remove(b.id)} role="button" style={{ cursor: "pointer", color: "var(--muted)", fontSize: 12 }} title="remove">✕</span>
                  </span>
                </div>
                <div style={{ height: 6, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}>
                  <div style={{ width: Math.min(100, b.pct) + "%", height: "100%", background: barColor }} />
                </div>
                {b.breached && <div style={{ fontSize: 11, color: "var(--error)", marginTop: 3 }}>Over budget by ${(b.spend - b.limit_usd).toFixed(2)}.</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* Shared sortable-table helper for the cost tables. */
function useSort<T extends Record<string, unknown>>(rows: T[], defaultKey: string) {
  const [key, setKey] = useState<string>(defaultKey);
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const sorted = useMemo(() => {
    const arr = [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (typeof av === "number" && typeof bv === "number") return av - bv;
      return String(av).localeCompare(String(bv));
    });
    return dir === "desc" ? arr.reverse() : arr;
  }, [rows, key, dir]);
  const toggle = (k: string) => { if (k === key) setDir((d) => (d === "desc" ? "asc" : "desc")); else { setKey(k); setDir("desc"); } };
  return { sorted, key, dir, toggle };
}

function SortTh({ label, k, sort, align = "right" }: { label: string; k: string; sort: { key: string; dir: string; toggle: (k: string) => void }; align?: "left" | "right" }) {
  return (
    <th onClick={() => sort.toggle(k)} className={align === "right" ? "r" : ""} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}>
      {label}
      {/* zero-width so the label's right edge stays aligned with the cells */}
      <span style={{ display: "inline-block", width: 0, overflow: "visible", paddingLeft: 4, fontSize: 9, color: sort.key === k ? "var(--accent)" : "transparent" }}>
        {sort.key === k ? (sort.dir === "desc" ? "▼" : "▲") : "▼"}
      </span>
    </th>
  );
}

/* ---- overview ---- */
function OverviewLevel({ s, data, coverage, modules, moduleMax, onModule }: any) {
  const sort = useSort(modules, "cost");
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
      <BudgetsPanel />

      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>By module</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>Click a module to drill into its prompts.</div>
        </div>
        <table className="pr">
          <thead><tr>
            <SortTh label="Module" k="module" sort={sort} align="left" />
            <SortTh label="Calls" k="calls" sort={sort} />
            <SortTh label="Tokens in" k="tokens_in" sort={sort} />
            <th>Share</th>
            <SortTh label="Cost" k="cost" sort={sort} />
            <th className="r"></th>
          </tr></thead>
          <tbody>
            {sort.sorted.map((r: any) => (
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
  const prompts = data.by_name.filter((b: any) => moduleOf(b.name) === module);
  const sort = useSort(prompts, "cost");
  const [q, setQ] = useState("");
  const shown = sort.sorted.filter((p: any) => p.name.toLowerCase().includes(q.trim().toLowerCase()));
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
        <div style={{ padding: "11px 16px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Prompts by spend</span>
          {prompts.length > 8 && (
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search prompts…"
              style={{ background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 6, padding: "5px 10px", color: "var(--text)", fontSize: 12, outline: "none", width: 180 }} />
          )}
        </div>
        <div style={{ maxHeight: 560, overflowY: "auto" }}>
        <table className="pr">
          <thead><tr>
            <SortTh label="Prompt" k="name" sort={sort} align="left" />
            <SortTh label="Calls" k="calls" sort={sort} />
            <SortTh label="Tokens in" k="tokens_in" sort={sort} />
            <SortTh label="Tokens out" k="tokens_out" sort={sort} />
            <th>Share</th>
            <SortTh label="Cost" k="cost" sort={sort} />
            <th className="r"></th>
          </tr></thead>
          <tbody>
            {shown.map((p: any) => (
              <tr key={p.name} onClick={() => onPrompt(p.name)} style={{ cursor: "pointer" }}>
                <td style={{ fontWeight: 600, fontSize: 13 }}><span style={{ color: "var(--muted)" }}>{module}.</span>{p.name.slice(p.name.indexOf(".") + 1)}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{p.calls.toLocaleString()}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{formatTokens(p.tokens_in)}</td>
                <td className="r mono" style={{ color: "var(--text-dim)" }}>{formatTokens(p.tokens_out)}</td>
                <td><div style={{ width: 90, height: 5, background: "var(--bg-elev)", borderRadius: 3, overflow: "hidden", border: "1px solid var(--border)" }}><div style={{ width: (p.cost / max) * 100 + "%", height: "100%", background: "var(--accent)", opacity: 0.85 }} /></div></td>
                <td className="r mono" style={{ color: "var(--accent)", fontWeight: 600 }}>${p.cost.toFixed(2)}</td>
                <td className="r"><span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>›</span></td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </div>
    </>
  );
}

/* ---- prompt: cost stats + cost-sorted invocations ---- */
function PromptCostLevel({ prompt, module, days }: { prompt: string; module: string; days: number }) {
  const { data: stats } = useCached<PromptStats>(`stats:${prompt}:${days}`, () => getPromptStats(prompt, days));
  const short = prompt.slice(prompt.indexOf(".") + 1);
  // Breadcrumb path to hand to the invocation page so "back" returns here.
  const crumbs = [
    { label: "cost", to: "/cost" },
    { label: module, to: `/cost?module=${encodeURIComponent(module)}` },
    { label: short, to: `/cost?module=${encodeURIComponent(module)}&prompt=${encodeURIComponent(prompt)}` },
  ];
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
      <InvocationsPanel name={prompt} order="cost" days={days} crumbs={crumbs}
        emptyHint="No invocations captured for this prompt in the window." />
    </>
  );
}
