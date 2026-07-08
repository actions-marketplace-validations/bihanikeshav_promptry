import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader, Select } from "../components/ui";
import { runPlaygroundModel, getConfig, getPrompts, getPromptContent } from "../api/client";
import { templateVars } from "../utils";
import { TemplateEditor } from "../components/TemplateEditor";
import type { PlaygroundRuleType } from "../api/types";

interface Rule {
  id: number;
  type: PlaygroundRuleType;
  value: string;
}

/* -------- models (display metadata; server uses the id) -------- */
interface ModelMeta {
  id: string;
  label: string;
  inCost: number;
  outCost: number;
}

const MODELS: ModelMeta[] = [
  { id: "gpt-4o", label: "gpt-4o", inCost: 2.5, outCost: 10 },
  { id: "gpt-4o-mini", label: "gpt-4o-mini", inCost: 0.15, outCost: 0.6 },
  { id: "claude-haiku-4-5", label: "claude-haiku-4-5", inCost: 0.25, outCost: 1.25 },
  { id: "claude-sonnet-4-5", label: "claude-sonnet-4-5", inCost: 3, outCost: 15 },
  { id: "llama-3.3-70b", label: "llama-3.3-70b", inCost: 0.59, outCost: 0.79 },
];

/* -------- assertion types + client-side evaluator -------- */
interface RuleTypeDef {
  v: PlaygroundRuleType;
  label: string;
  weight: number;
  hint: string;
}

const RULE_TYPES: RuleTypeDef[] = [
  { v: "contains", label: "Must contain", weight: 1.0, hint: "keyword1, keyword2" },
  { v: "not_contains", label: "Must NOT contain", weight: 1.0, hint: "banned, term" },
  { v: "json_valid", label: "Valid JSON", weight: 0.5, hint: "" },
  { v: "json_path_eq", label: "JSON field equals", weight: 1.5, hint: "path.to.key=value" },
  { v: "matches", label: "Regex match", weight: 1.0, hint: "^\\{.*\\}$" },
  { v: "max_tokens", label: "Max tokens ≤", weight: 0.5, hint: "120" },
  { v: "similarity", label: "Similar to golden", weight: 1.5, hint: "(uses golden)" },
];

interface RuleResult {
  passed: boolean;
  score: number;
  detail: string;
}

function evalRule(r: Rule, response: string, golden: string): RuleResult {
  if (r.type === "contains") {
    const kws = r.value.split(",").map((x) => x.trim()).filter(Boolean);
    if (!kws.length) return { passed: true, score: 1, detail: "(no keywords)" };
    const hits = kws.filter((k) => response.toLowerCase().includes(k.toLowerCase()));
    const passed = hits.length === kws.length;
    return { passed, score: hits.length / kws.length, detail: `${hits.length}/${kws.length} keywords found` };
  }
  if (r.type === "not_contains") {
    const kws = r.value.split(",").map((x) => x.trim()).filter(Boolean);
    const hits = kws.filter((k) => response.toLowerCase().includes(k.toLowerCase()));
    const passed = hits.length === 0;
    return { passed, score: passed ? 1 : 0, detail: passed ? "no banned terms" : `found: ${hits.join(", ")}` };
  }
  if (r.type === "json_valid") {
    try {
      JSON.parse(response);
      return { passed: true, score: 1, detail: "parses as JSON" };
    } catch {
      return { passed: false, score: 0, detail: "JSON parse error" };
    }
  }
  if (r.type === "matches") {
    try {
      const passed = new RegExp(r.value).test(response);
      return { passed, score: passed ? 1 : 0, detail: passed ? "pattern matched" : "no match" };
    } catch {
      return { passed: false, score: 0, detail: "invalid regex" };
    }
  }
  if (r.type === "json_path_eq") {
    try {
      const parsed = JSON.parse(response);
      const [pathRaw, expected] = r.value.split("=").map((x) => x.trim());
      let cur: unknown = parsed;
      for (const seg of pathRaw.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean)) {
        cur = (cur as Record<string, unknown> | undefined)?.[seg];
      }
      const passed = String(cur) === expected;
      return {
        passed,
        score: passed ? 1 : 0,
        detail: passed ? `${pathRaw} == ${expected}` : `got ${JSON.stringify(cur)}`,
      };
    } catch {
      return { passed: false, score: 0, detail: "could not parse/path" };
    }
  }
  if (r.type === "max_tokens") {
    const limit = parseInt(r.value, 10) || 0;
    const tokens = Math.ceil(response.length / 3.8);
    const passed = tokens <= limit;
    return { passed, score: passed ? 1 : Math.max(0, limit / tokens), detail: `${tokens} / ${limit} token budget` };
  }
  if (r.type === "similarity") {
    const target = r.value || golden;
    const a = new Set(response.toLowerCase().split(/\W+/).filter(Boolean));
    const b = new Set(target.toLowerCase().split(/\W+/).filter(Boolean));
    const inter = [...a].filter((x) => b.has(x)).length;
    const union = new Set([...a, ...b]).size || 1;
    const score = inter / union;
    return { passed: score >= 0.5, score, detail: `~${(score * 100).toFixed(0)}% overlap vs. reference` };
  }
  return { passed: false, score: 0, detail: "unknown rule" };
}

function interpolate(tmpl: string, vars: Record<string, string>): string {
  // Value-driven: replace ONLY supplied variable names, in any of {{name}},
  // {name}, ${name}, $name. JSON braces and unknown placeholders are untouched
  // (mirrors promptry.prompts._substitute on the backend).
  const names = Object.keys(vars);
  if (!names.length) return tmpl;
  const alt = names
    .sort((a, b) => b.length - a.length)
    .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  const re = new RegExp(
    `\\{\\{\\s*(${alt})\\s*\\}\\}|\\$\\{(${alt})\\}|\\{\\s*(${alt})\\s*\\}|\\$(${alt})(?![A-Za-z0-9_])`,
    "g"
  );
  return tmpl.replace(re, (_m, a, b, c, d) => vars[a ?? b ?? c ?? d]);
}

/* -------- per-model run record -------- */
interface AssertionDisplay extends Rule {
  weight: number;
  passed: boolean;
  score: number;
  detail: string;
}

interface ModelRunRecord {
  body: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  assertions: AssertionDisplay[];
  overall_score: number;
  overall_passed: boolean;
  pass_count: number;
  error?: string;
}

/* -------- component -------- */
export default function Playground() {
  const [searchParams] = useSearchParams();
  const [sys, setSys] = useState("");
  const [user, setUser] = useState("");
  const [vars, setVars] = useState<Record<string, string>>({});
  const [context, setContext] = useState("");
  const [golden, setGolden] = useState("");
  const [rules, setRules] = useState<Rule[]>([]);
  const [models, setModels] = useState<string[]>(["gpt-4o-mini", "claude-haiku-4-5"]);
  const [temp, setTemp] = useState(0.2);
  const [results, setResults] = useState<Record<string, ModelRunRecord>>({});
  const [running, setRunning] = useState(false);
  const [tab, setTab] = useState<"prompt" | "context" | "rules">("prompt");
  const [focusedModel, setFocusedModel] = useState<string | null>(null);
  // Real tracked prompts you can load into the editor (vs the sample presets).
  const [registry, setRegistry] = useState<string[]>([]);
  const [loadedPrompt, setLoadedPrompt] = useState<string | null>(null);
  // A/B: a pinned run to compare the current (edited) prompt against.
  const [variantA, setVariantA] = useState<{ label: string; results: Record<string, ModelRunRecord> } | null>(null);
  // Model picker is driven by the project config (.promptry/config.toml) so it
  // reflects the models this project actually set up; the hardcoded MODELS list
  // is the fallback (fresh project) and a source of display metadata/pricing.
  const [modelMetas, setModelMetas] = useState<ModelMeta[]>(MODELS);

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        if (!cfg.models?.length) return; // no config yet — keep MODELS defaults
        const metas: ModelMeta[] = cfg.models.map((m) => {
          const known = MODELS.find((k) => k.id === m.id);
          return {
            id: m.id,
            label: m.label || known?.label || m.id,
            inCost: known?.inCost ?? 0,
            outCost: known?.outCost ?? 0,
          };
        });
        setModelMetas(metas);
        // Reconcile selection: keep any still-valid picks, else default to first two.
        setModels((cur) => {
          const ids = metas.map((x) => x.id);
          const valid = cur.filter((c) => ids.includes(c));
          return valid.length ? valid : metas.slice(0, 2).map((x) => x.id);
        });
      })
      .catch(() => {});
  }, []);

  // Load a real tracked prompt's current content into the system field.
  const loadFromRegistry = async (name: string) => {
    if (!name) return;
    try {
      const c = await getPromptContent(name);
      setSys(c.content);
      setResults({});
      setFocusedModel(null);
      setLoadedPrompt(name);
    } catch { /* ignore */ }
  };

  // Load the list of real tracked prompts; auto-load the first so the editor
  // starts on real content instead of a blank or hardcoded sample.
  useEffect(() => {
    getPrompts()
      .then((ps) => {
        const names = ps.map((p) => p.name);
        setRegistry(names);
        // Honor a ?prompt=<name> deep-link (e.g. from the Duplicates page),
        // otherwise start on the first tracked prompt.
        const requested = searchParams.get("prompt");
        const initial = requested && names.includes(requested) ? requested : names[0];
        if (initial) loadFromRegistry(initial);
      })
      .catch(() => setRegistry([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Pin the current results as variant A to compare the next run against.
  const pinVariantA = () => {
    if (!Object.keys(results).length) return;
    setVariantA({ label: loadedPrompt || "variant A", results: { ...results } });
  };

  const resolvedUser = interpolate(user, vars);
  // Variables referenced by EITHER the system or user prompt ({{}} or legacy $).
  const varKeys = useMemo(
    () => templateVars(sys + "\n" + user),
    [sys, user]
  );

  const addRule = () =>
    setRules([...rules, { id: Date.now(), type: "contains", value: "" }]);
  const updateRule = (id: number, patch: Partial<Rule>) =>
    setRules(rules.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const removeRule = (id: number) => setRules(rules.filter((r) => r.id !== id));

  const runAll = async () => {
    setRunning(true);
    setResults({});
    const out: Record<string, ModelRunRecord> = {};
    for (const mId of models) {
      try {
        const resp = await runPlaygroundModel({
          model: mId,
          system: interpolate(sys, vars),
          user: resolvedUser,
          context,
          temperature: temp,
        });
        const assertions: AssertionDisplay[] = rules.map((r) => {
          const def = RULE_TYPES.find((t) => t.v === r.type);
          const ev = evalRule(r, resp.response, golden);
          return { ...r, ...ev, weight: def?.weight ?? 1 };
        });
        const totalWeight = assertions.reduce((a, b) => a + b.weight, 0) || 1;
        const weightedScore =
          assertions.reduce((a, b) => a + b.score * b.weight, 0) / totalWeight;
        const pass_count = assertions.filter((a) => a.passed).length;
        out[mId] = {
          body: resp.response,
          latency_ms: resp.latency_ms,
          tokens_in: resp.tokens_in,
          tokens_out: resp.tokens_out,
          cost: resp.cost,
          assertions,
          overall_score: weightedScore,
          overall_passed: pass_count === rules.length,
          pass_count,
        };
      } catch (e) {
        out[mId] = {
          body: "",
          latency_ms: 0,
          tokens_in: 0,
          tokens_out: 0,
          cost: 0,
          assertions: [],
          overall_score: 0,
          overall_passed: false,
          pass_count: 0,
          error: String(e),
        };
      }
      setResults({ ...out });
    }
    setFocusedModel(models[0] ?? null);
    setRunning(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        runAll();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sys, user, vars, context, rules, models, golden, temp]);

  const toggleModel = (id: string) =>
    setModels(models.includes(id) ? models.filter((x) => x !== id) : [...models, id]);

  return (
    <div>
      <PageHeader
        eyebrow="~/promptry · playground"
        title="Prompt Playground"
        description="Iterate on a prompt, try it across models, and preview assertion results before promoting to a suite."
        tags={[loadedPrompt || "scratch", `${models.length} model${models.length === 1 ? "" : "s"}`, `${rules.length} assertions`]}
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {variantA ? (
              <button className="btn" onClick={() => setVariantA(null)} title="Stop comparing against variant A"
                style={{ fontSize: 11.5 }}>✕ A/B: {variantA.label}</button>
            ) : (
              <button className="btn" onClick={pinVariantA} disabled={!Object.keys(results).length}
                title="Pin this run as variant A, then edit the prompt and run again to compare"
                style={{ fontSize: 11.5 }}>⊞ Pin as A</button>
            )}
          <button
            className="btn btn-primary"
            onClick={runAll}
            disabled={running || !models.length}
          >
            {running ? (
              <>
                <span
                  className="live-dot"
                  style={{ width: 6, height: 6, background: "#1a0e04" }}
                />
                Running…
              </>
            ) : (
              <>
                Run
                <span
                  className="kbd"
                  style={{
                    marginLeft: 4,
                    background: "rgba(26,14,4,0.2)",
                    borderColor: "rgba(26,14,4,0.3)",
                    color: "#1a0e04",
                  }}
                >
                  ⌘↵
                </span>
              </>
            )}
          </button>
          </div>
        }
      />

      {/* Control bar: prompt source (left) + models to compare (right) */}
      <div
        className="card"
        style={{ padding: "8px 12px", marginBottom: 12, display: "flex", gap: 16, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}
      >
        <div style={{ display: "flex", gap: 9, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", fontFamily: "var(--font-mono)" }}>
            Prompt
          </span>
          {registry.length > 0 ? (
            <Select
              value={loadedPrompt ?? ""}
              onChange={(v) => loadFromRegistry(v)}
              minWidth={230}
              searchable
              options={registry.map((n) => ({ value: n, label: n }))}
            />
          ) : (
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              none tracked — type below or wrap one with <span className="mono">track()</span>
            </span>
          )}
        </div>

        {/* Models to compare — toggle pills */}
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", fontFamily: "var(--font-mono)" }}>
            Models
          </span>
          {modelMetas.map((m) => {
            const on = models.includes(m.id);
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => toggleModel(m.id)}
                title={`$${m.inCost.toFixed(2)}/M in · $${m.outCost.toFixed(2)}/M out`}
                className="mono"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 7,
                  padding: "5px 11px", borderRadius: 999, fontSize: 11.5, lineHeight: 1,
                  cursor: "pointer", whiteSpace: "nowrap",
                  border: "1px solid " + (on ? "var(--accent-line)" : "var(--border)"),
                  background: on ? "var(--accent-soft)" : "var(--bg-elev)",
                  color: on ? "var(--accent)" : "var(--text-dim)",
                  transition: "border-color .12s, color .12s, background .12s",
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: 999, background: on ? "var(--accent)" : "var(--border)" }} />
                {m.label}
              </button>
            );
          })}
        </div>
      </div>

      {variantA && Object.keys(results).length > 0 && (
        <div className="card" style={{ padding: "10px 14px", marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)", marginBottom: 8 }}>
            A/B · <span style={{ color: "var(--text-dim)" }}>{variantA.label}</span> (A) vs current (B)
          </div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {models.filter((m) => variantA.results[m] && results[m]).map((m) => {
              const a = variantA.results[m].overall_score;
              const b = results[m].overall_score;
              const d = b - a;
              const col = Math.abs(d) < 0.005 ? "var(--muted)" : d > 0 ? "var(--success)" : "var(--error)";
              return (
                <div key={m} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>{modelMetas.find((x) => x.id === m)?.label ?? m}</span>
                  <span style={{ fontSize: 13 }}>
                    <span style={{ color: "var(--muted)" }}>{(a * 100).toFixed(0)}%</span>
                    <span style={{ color: "var(--muted)", margin: "0 5px" }}>→</span>
                    <span style={{ fontWeight: 600 }}>{(b * 100).toFixed(0)}%</span>
                    <span className="mono" style={{ color: col, fontSize: 11.5, marginLeft: 6 }}>{d >= 0 ? "+" : ""}{(d * 100).toFixed(0)}</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)", gap: 14 }}>
        {/* LEFT: editor */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <div className="card" style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", borderBottom: "1px solid var(--border)", background: "var(--bg-elev)" }}>
              {(
                [
                  { v: "prompt" as const, l: "Prompt", c: `${sys.length + user.length}` },
                  { v: "context" as const, l: "Context", c: context ? `${context.length}` : "—" },
                  { v: "rules" as const, l: "Assertions", c: `${rules.length}` },
                ]
              ).map((t) => {
                const active = tab === t.v;
                return (
                  <button
                    key={t.v}
                    onClick={() => setTab(t.v)}
                    className="btn btn-ghost"
                    style={{
                      borderRadius: 0,
                      borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                      color: active ? "var(--text)" : "var(--secondary)",
                      padding: "10px 14px",
                      fontWeight: active ? 600 : 500,
                      fontSize: 12.5,
                      gap: 8,
                    }}
                  >
                    {t.l}
                    <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>
                      {t.c}
                    </span>
                  </button>
                );
              })}
            </div>

            {tab === "prompt" && (
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>
                      System
                    </div>
                    <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>
                      {sys.length} chars
                    </span>
                  </div>
                  <TemplateEditor value={sys} onChange={setSys} minHeight={96} />
                </div>
                <div>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>
                      User message
                    </div>
                    <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>
                      use <span style={{ color: "var(--accent)" }}>{"{{name}}"}</span> for variables
                    </span>
                  </div>
                  <TemplateEditor value={user} onChange={setUser} minHeight={72} />
                </div>
                {varKeys.length > 0 && (
                  <div>
                    <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, fontFamily: "var(--font-mono)" }}>
                      Variables
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
                      {varKeys.map((k) => (
                        <div
                          key={k}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            background: "var(--bg-elev)",
                            border: "1px solid var(--border)",
                            borderRadius: 6,
                            padding: "4px 8px",
                          }}
                        >
                          <span className="mono" style={{ fontSize: 11, color: "var(--accent)" }}>
                            {k}
                          </span>
                          <span style={{ color: "var(--muted)" }}>=</span>
                          <input
                            className="inp"
                            style={{ flex: 1, minWidth: 0, padding: "2px 6px", border: "none", background: "transparent" }}
                            value={vars[k] || ""}
                            onChange={(e) => setVars({ ...vars, [k]: e.target.value })}
                          />
                        </div>
                      ))}
                    </div>
                    <div
                      style={{
                        marginTop: 8,
                        padding: 8,
                        background: "var(--bg-elev)",
                        border: "1px dashed var(--border-strong)",
                        borderRadius: 4,
                        fontFamily: "var(--font-mono)",
                        fontSize: 11.5,
                        color: "var(--text-dim)",
                      }}
                    >
                      <span style={{ color: "var(--muted)" }}>resolves → </span>
                      {resolvedUser}
                    </div>
                  </div>
                )}
                <div style={{ display: "flex", gap: 18, paddingTop: 6, borderTop: "1px solid var(--border)" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--secondary)" }}>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                      }}
                    >
                      temp
                    </span>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={temp}
                      onChange={(e) => setTemp(parseFloat(e.target.value))}
                      style={{ accentColor: "var(--accent)", width: 110 }}
                    />
                    <span className="mono" style={{ color: "var(--text)", width: 30 }}>
                      {temp.toFixed(2)}
                    </span>
                  </label>
                </div>
              </div>
            )}

            {tab === "context" && (
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "var(--font-mono)" }}>
                      Retrieved context
                    </div>
                    <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>
                      ~{Math.ceil(context.length / 3.8)} tokens
                    </span>
                  </div>
                  <textarea
                    className="inp"
                    value={context}
                    onChange={(e) => setContext(e.target.value)}
                    rows={10}
                    style={{ width: "100%" }}
                    placeholder="Paste RAG context / retrieved docs here…"
                  />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, fontFamily: "var(--font-mono)" }}>
                    Golden response (for similarity)
                  </div>
                  <textarea
                    className="inp"
                    value={golden}
                    onChange={(e) => setGolden(e.target.value)}
                    rows={3}
                    style={{ width: "100%" }}
                    placeholder="Expected model output — used by the similarity assertion."
                  />
                </div>
              </div>
            )}

            {tab === "rules" && (
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                {rules.map((r) => {
                  const t = RULE_TYPES.find((x) => x.v === r.type) || RULE_TYPES[0];
                  return (
                    <div
                      key={r.id}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "auto 1fr auto auto",
                        gap: 8,
                        alignItems: "center",
                        padding: "6px 0",
                      }}
                    >
                      <Select
                        value={r.type}
                        onChange={(v) => updateRule(r.id, { type: v as PlaygroundRuleType })}
                        minWidth={170}
                        options={RULE_TYPES.map((x) => ({ value: x.v, label: x.label }))}
                      />
                      {r.type === "json_valid" ? (
                        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
                          Checks the response parses as JSON.
                        </span>
                      ) : r.type === "similarity" ? (
                        <span style={{ fontSize: 11.5, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                          compares against golden (Context tab)
                        </span>
                      ) : (
                        <input
                          className="inp"
                          value={r.value}
                          onChange={(e) => updateRule(r.id, { value: e.target.value })}
                          placeholder={t.hint}
                          style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                        />
                      )}
                      <span
                        className="mono"
                        style={{ fontSize: 10, color: "var(--muted)", padding: "0 4px" }}
                        data-tt={`weight ×${t.weight}`}
                      >
                        w={t.weight}
                      </span>
                      <button
                        className="btn btn-ghost"
                        onClick={() => removeRule(r.id)}
                        style={{ color: "var(--muted)", padding: "4px 8px" }}
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
                <button
                  className="btn"
                  onClick={addRule}
                  style={{ alignSelf: "flex-start", color: "var(--accent)", borderColor: "var(--accent-line)" }}
                >
                  + Add assertion
                </button>
              </div>
            )}
          </div>

        </div>

        {/* RIGHT: runs */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          {!Object.keys(results).length && !running && (
            <div
              className="card"
              style={{
                padding: 40,
                textAlign: "center",
                color: "var(--muted)",
                border: "1px dashed var(--border-strong)",
                background: "transparent",
              }}
            >
              <svg width="40" height="40" viewBox="0 0 40 40" style={{ marginBottom: 12, opacity: 0.5 }}>
                <rect
                  x="2"
                  y="2"
                  width="36"
                  height="36"
                  rx="8"
                  stroke="var(--border-strong)"
                  strokeWidth="1.5"
                  fill="none"
                  strokeDasharray="3 3"
                />
                <path d="M14 20 L18 24 L26 16" stroke="var(--accent)" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <div style={{ fontSize: 13, color: "var(--text-dim)", marginBottom: 4 }}>No runs yet</div>
              <div style={{ fontSize: 11.5 }}>
                Click <span className="kbd">⌘</span> <span className="kbd">↵</span> to run across {models.length}{" "}
                model{models.length === 1 ? "" : "s"}
              </div>
            </div>
          )}

          {models.map((mId) => {
            const m = modelMetas.find((x) => x.id === mId) ?? { id: mId, label: mId, inCost: 0, outCost: 0 };
            const r = results[mId];
            const isFocused = focusedModel === mId;
            const isLoading = running && !r;
            return (
              <div
                key={mId}
                className={`card ${r ? "enter" : ""}`}
                style={{
                  padding: 0,
                  overflow: "hidden",
                  borderColor: isFocused ? "var(--accent-line)" : "var(--border)",
                  boxShadow: isFocused ? "0 0 0 1px var(--accent-line)" : "none",
                }}
              >
                <div
                  onClick={() => r && setFocusedModel(isFocused ? null : mId)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "10px 14px",
                    background: "var(--bg-elev)",
                    borderBottom: "1px solid var(--border)",
                    cursor: r ? "pointer" : "default",
                  }}
                >
                  <span className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)", flex: 1 }}>
                    {m.label}
                  </span>
                  {isLoading && (
                    <span style={{ fontSize: 11, color: "var(--accent)", display: "flex", alignItems: "center", gap: 6 }}>
                      <span
                        className="live-dot"
                        style={{ background: "var(--accent)", boxShadow: "0 0 0 3px rgba(251,146,60,0.2)" }}
                      />
                      running…
                    </span>
                  )}
                  {r && !r.error && (
                    <>
                      <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }} data-tt="latency">
                        {r.latency_ms}ms
                      </span>
                      <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }} data-tt="tokens in/out">
                        {r.tokens_in}→{r.tokens_out}
                      </span>
                      <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }} data-tt="cost">
                        ${r.cost.toFixed(4)}
                      </span>
                      <span
                        className="mono"
                        style={{
                          fontSize: 11,
                          fontWeight: 700,
                          padding: "3px 8px",
                          borderRadius: 4,
                          background: r.overall_passed ? "var(--success-soft)" : "var(--error-soft)",
                          color: r.overall_passed ? "var(--success)" : "var(--error)",
                        }}
                      >
                        {(r.overall_score * 100).toFixed(0)}% · {r.pass_count}/{r.assertions.length}
                      </span>
                    </>
                  )}
                  {r?.error && (
                    <span
                      className="mono"
                      style={{
                        fontSize: 11,
                        padding: "3px 8px",
                        borderRadius: 4,
                        background: "var(--error-soft)",
                        color: "var(--error)",
                      }}
                    >
                      ERROR
                    </span>
                  )}
                </div>

                {isLoading && (
                  <div style={{ padding: 14 }}>
                    <div className="skeleton" style={{ height: 10, width: "85%", borderRadius: 3, marginBottom: 8 }} />
                    <div className="skeleton" style={{ height: 10, width: "62%", borderRadius: 3, marginBottom: 8 }} />
                    <div className="skeleton" style={{ height: 10, width: "72%", borderRadius: 3 }} />
                  </div>
                )}

                {r?.error && (
                  <div
                    style={{
                      padding: 14,
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                      color: "var(--error)",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {r.error}
                  </div>
                )}

                {r && !r.error && (
                  <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
                    <pre
                      className="mono"
                      style={{
                        margin: 0,
                        fontSize: 12.5,
                        color: "var(--text-dim)",
                        whiteSpace: "pre-wrap",
                        lineHeight: 1.6,
                        background: "var(--bg)",
                        padding: 12,
                        borderRadius: 6,
                        border: "1px solid var(--border)",
                        maxHeight: isFocused ? 320 : 120,
                        overflow: "auto",
                        transition: "max-height .2s ease",
                      }}
                    >
                      {r.body}
                    </pre>

                    <div
                      style={{
                        display: "flex",
                        gap: 2,
                        height: 4,
                        borderRadius: 2,
                        overflow: "hidden",
                        background: "var(--bg-elev)",
                      }}
                    >
                      {r.assertions.map((a, i) => (
                        <div
                          key={i}
                          style={{
                            flex: a.weight,
                            background: a.passed ? "var(--success)" : "var(--error)",
                            opacity: 0.3 + a.score * 0.7,
                          }}
                          data-tt={`${a.type}: ${a.detail}`}
                        />
                      ))}
                    </div>

                    {isFocused && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {r.assertions.map((a, i) => (
                          <div
                            key={i}
                            style={{
                              display: "grid",
                              gridTemplateColumns: "42px 1fr auto auto",
                              gap: 10,
                              alignItems: "center",
                              padding: "6px 10px",
                              borderRadius: 4,
                              fontSize: 12,
                              background: a.passed ? "var(--success-soft)" : "var(--error-soft)",
                              border: `1px solid ${
                                a.passed ? "rgba(74,222,128,0.18)" : "rgba(248,113,113,0.18)"
                              }`,
                            }}
                          >
                            <span
                              className="mono"
                              style={{
                                fontSize: 10.5,
                                fontWeight: 700,
                                color: a.passed ? "var(--success)" : "var(--error)",
                              }}
                            >
                              {a.passed ? "PASS" : "FAIL"}
                            </span>
                            <span
                              style={{
                                fontFamily: "var(--font-mono)",
                                color: "var(--text-dim)",
                                fontSize: 11.5,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                              }}
                            >
                              {a.type}
                              {a.value && (
                                <span style={{ color: "var(--muted)" }}>
                                  {" · "}
                                  {a.value.length > 38 ? a.value.slice(0, 38) + "…" : a.value}
                                </span>
                              )}
                            </span>
                            <span style={{ fontSize: 10.5, color: "var(--muted)", fontStyle: "italic" }}>
                              {a.detail}
                            </span>
                            <span
                              className="mono"
                              style={{
                                fontSize: 11,
                                color: a.passed ? "var(--success)" : "var(--error)",
                                fontWeight: 600,
                                width: 36,
                                textAlign: "right",
                              }}
                            >
                              {(a.score * 100).toFixed(0)}%
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {Object.values(results).filter((r) => !r.error).length >= 2 && !running && (
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
                Comparison
              </div>
              <table className="pr" style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="r">Score</th>
                    <th className="r">Latency</th>
                    <th className="r">Cost</th>
                    <th className="r">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(results)
                    .filter(([, r]) => !r.error)
                    .sort((a, b) => b[1].overall_score - a[1].overall_score)
                    .map(([mId, r], i) => (
                      <tr key={mId}>
                        <td style={{ fontFamily: "var(--font-mono)" }}>
                          {i === 0 && (
                            <span className="mono" style={{ color: "var(--accent)", marginRight: 6 }}>
                              ▸
                            </span>
                          )}
                          {modelMetas.find((x) => x.id === mId)?.label ?? mId}
                        </td>
                        <td
                          className="mono"
                          style={{
                            textAlign: "right",
                            color: r.overall_passed ? "var(--success)" : "var(--error)",
                            fontWeight: 600,
                          }}
                        >
                          {(r.overall_score * 100).toFixed(0)}%
                        </td>
                        <td className="mono" style={{ textAlign: "right", color: "var(--text-dim)" }}>
                          {r.latency_ms}ms
                        </td>
                        <td className="mono" style={{ textAlign: "right", color: "var(--text-dim)" }}>
                          ${r.cost.toFixed(4)}
                        </td>
                        <td className="mono" style={{ textAlign: "right", color: "var(--muted)" }}>
                          {r.tokens_in}→{r.tokens_out}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
