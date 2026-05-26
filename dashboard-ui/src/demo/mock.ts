/**
 * Demo mode: a fetch interceptor that serves synthetic data for /api/* so the
 * REAL dashboard runs as a static, backend-less demo (GitHub Pages). Nothing
 * in the app code changes — installDemoFetch() just monkeypatches window.fetch
 * when the build flag VITE_DEMO is set, so the demo can never drift from the
 * product. All data below is fictional.
 */

const now = Date.now();
const iso = (daysAgo: number, h = 0) => new Date(now - daysAgo * 864e5 - h * 36e5).toISOString();
const pick = <T>(a: T[], i: number) => a[i % a.length];

// ---- prompts (note {{var}} syntax — shows the live highlighting) ----
const CONTENT: Record<string, string> = {
  "rag.answer": `You are a support assistant for {{product}}.
Answer the question using ONLY the context below. If it isn't supported, say "I don't know."

Context:
{{context}}

Question: {{question}}
Return JSON: {"answer": "...", "sources": ["..."]}`,
  "rag.rerank": `Rank these {{k}} passages by relevance to the query "{{query}}".
Return a JSON array of passage ids, best first.`,
  "agent.planner": `You are the planning step for a {{role}} agent.
Goal: {{goal}}
Available tools: {{tools}}
Think step by step, then emit the next action as JSON.`,
  "support.classify": `Classify the message into exactly one of: billing, bug, feature_request, account, other.
Message: {{message}}
Reply with JSON {"intent": "...", "confidence": 0.0-1.0}.`,
  "summary.tldr": `Summarize the document below into {{n}} crisp bullet points for an executive.

{{document}}`,
};
const VERSIONS: Record<string, number> = { "rag.answer": 3, "rag.rerank": 1, "agent.planner": 2, "support.classify": 2, "summary.tldr": 1 };
const TAGS: Record<string, string[]> = { "rag.answer": ["prod"], "agent.planner": ["staging"], "support.classify": ["prod"] };
const NAMES = Object.keys(CONTENT);

const promptSummaries = NAMES.map((name) => ({ name, latest_version: VERSIONS[name], tags: TAGS[name] || [] }));

function vars(name: string): string[] {
  return [...(CONTENT[name] || "").matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]);
}
function contentResp(name: string, v?: number) {
  return {
    name, version: v ?? VERSIONS[name], content: CONTENT[name] || "",
    hash: "demo" + (v ?? VERSIONS[name]), created_at: iso(3), tags: TAGS[name] || [],
    variables: vars(name),
    lint: name === "rag.answer" ? [{ level: "info", message: "Good: output format is specified (JSON)." }] : [],
  };
}

// ---- suites ----
const SUITES = [
  { name: "rag-quality", latest_score: 0.913, passed: true, model_version: "gpt-4o-mini", prompt_version: 3, timestamp: iso(0, 2), drift_status: "stable", drift_slope: 0.002, sparkline_scores: [0.88, 0.9, 0.89, 0.91, 0.9, 0.913] },
  { name: "support-routing", latest_score: 0.955, passed: true, model_version: "gpt-4o-mini", prompt_version: 2, timestamp: iso(0, 5), drift_status: "stable", drift_slope: -0.001, sparkline_scores: [0.95, 0.96, 0.95, 0.96, 0.955, 0.955] },
  { name: "summary-faithfulness", latest_score: 0.844, passed: false, model_version: "claude-haiku-4-5", prompt_version: 1, timestamp: iso(1), drift_status: "drifting", drift_slope: -0.04, sparkline_scores: [0.9, 0.89, 0.88, 0.86, 0.85, 0.844] },
];
const suiteRuns = (name: string) =>
  Array.from({ length: 12 }, (_, i) => {
    const s = SUITES.find((x) => x.name === name)!;
    const base = s.latest_score;
    const score = Math.max(0.6, Math.min(0.99, base + (Math.sin(i) * 0.03) - i * (s.drift_status === "drifting" ? 0.004 : 0)));
    return { id: 100 + i, suite_name: name, prompt_name: NAMES[0], prompt_version: (i % 3) + 1, model_version: pick(["gpt-4o-mini", "claude-haiku-4-5"], i), timestamp: iso(12 - i), overall_pass: score >= 0.85, overall_score: Number(score.toFixed(3)) };
  }).reverse();

const ASSERT_TYPES = ["semantic", "contains", "grounded", "json_valid"];
function runDetail(name: string, runId: number) {
  const assertions = ASSERT_TYPES.map((t, i) => ({
    id: runId * 10 + i, run_id: runId, test_name: `case_${i + 1}`, assertion_type: t,
    passed: i !== 2 || name !== "summary-faithfulness", score: t === "json_valid" ? 1 : Number((0.78 + i * 0.05).toFixed(2)),
    details: t === "grounded" ? { judge_cost_estimated: true, judge_model: "gpt-4o-mini", judge_tokens_in: 320, judge_tokens_out: 12, judge_cost: 0.00006 } : { criteria: "demo" },
    latency_ms: 400 + i * 120,
  }));
  return {
    run: { id: runId, suite_name: name, prompt_name: NAMES[0], prompt_version: 3, model_version: "gpt-4o-mini", timestamp: iso(0, 2), overall_pass: name !== "summary-faithfulness", overall_score: SUITES.find((s) => s.name === name)?.latest_score ?? 0.9 },
    assertions,
    judge: { calls: 1, model: "gpt-4o-mini", tokens_in: 320, tokens_out: 12, cost: 0.00006, estimated: true, unpriced: false },
  };
}

// ---- invocations + cost ----
const MODELS = ["gpt-4o-mini", "gpt-4o", "claude-haiku-4-5"];
const RATE: Record<string, [number, number]> = { "gpt-4o-mini": [0.15, 0.6], "gpt-4o": [2.5, 10], "claude-haiku-4-5": [0.25, 1.25] };
const INVOCATIONS = Array.from({ length: 64 }, (_, i) => {
  const name = pick(NAMES, i * 3 + (i % 5));
  const model = pick(MODELS, i + (i % 3));
  const tin = 600 + ((i * 137) % 2400);
  const tout = 80 + ((i * 53) % 500);
  const [ri, ro] = RATE[model];
  const cost = (tin * ri + tout * ro) / 1e6;
  return {
    id: 5000 - i, prompt_name: name, prompt_version: (i % 3) + 1, created_at: iso(i % 14, i % 24),
    model, tokens_in: tin, tokens_out: tout, cost: Number(cost.toFixed(6)), latency_ms: 300 + ((i * 91) % 4200),
    has_capture: i % 3 === 0, output_preview: "Demo response preview for call #" + (5000 - i) + " …",
    rating: i % 4 === 0 ? Number((i % 8 === 0 ? 0.4 : 0.9).toFixed(2)) : null, comment: i % 8 === 0 ? "missed a figure" : null,
  };
});
function costResp(days: number, name?: string | null) {
  const rows = INVOCATIONS.filter((r) => (name ? r.prompt_name === name : true));
  const total_cost = rows.reduce((a, r) => a + (r.cost || 0), 0);
  const byNameMap: Record<string, any> = {};
  for (const r of rows) {
    const e = (byNameMap[r.prompt_name] ||= { name: r.prompt_name, calls: 0, tokens_in: 0, tokens_out: 0, cost: 0, models: new Set<string>() });
    e.calls++; e.tokens_in += r.tokens_in; e.tokens_out += r.tokens_out; e.cost += r.cost || 0; e.models.add(r.model);
  }
  const by_name = Object.values(byNameMap).map((e: any) => ({ ...e, cost: Number(e.cost.toFixed(5)), models: [...e.models] }));
  const byDate: Record<string, any> = {};
  for (const r of rows) {
    const d = r.created_at.slice(0, 10);
    const e = (byDate[d] ||= { date: d, calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 });
    e.calls++; e.tokens_in += r.tokens_in; e.tokens_out += r.tokens_out; e.cost += r.cost || 0;
  }
  return {
    summary: { total_cost: Number(total_cost.toFixed(4)), total_calls: rows.length, total_tokens_in: rows.reduce((a, r) => a + r.tokens_in, 0), total_tokens_out: rows.reduce((a, r) => a + r.tokens_out, 0), cache_hit_rate: 0.31, cache_savings: 0.42, avg_cost: rows.length ? total_cost / rows.length : 0 },
    by_name: by_name.sort((a, b) => b.cost - a.cost),
    by_date: Object.values(byDate).map((e: any) => ({ ...e, cost: Number(e.cost.toFixed(5)) })).sort((a, b) => a.date.localeCompare(b.date)),
  };
}

const CONFIG = {
  models: [
    { id: "gpt-4o-mini", provider: "openai", label: "GPT-4o mini" },
    { id: "gpt-4o", provider: "openai", label: "GPT-4o" },
    { id: "claude-haiku-4-5", provider: "anthropic", label: "Claude Haiku 4.5" },
  ],
  judge: { model: "gpt-4o-mini" },
  dashboard: { default_days: 14 },
  pricing: {},
  key_status: { openai: true, anthropic: true, xai: false, google: false, azure: false },
  path: "~/demo/.promptry/config.toml",
};

function invocationDetail(id: number) {
  const r = INVOCATIONS.find((x) => x.id === id) || INVOCATIONS[0];
  return {
    id: r.id, prompt_name: r.prompt_name, prompt_version: r.prompt_version, created_at: r.created_at,
    request_id: "demo-" + r.id, metadata: { model: r.model, tokens_in: r.tokens_in, tokens_out: r.tokens_out, cost: r.cost, latency_ms: r.latency_ms },
    input_text: `System: ${CONTENT[r.prompt_name]?.slice(0, 120) || "…"}\n\nUser: a real user question for call ${r.id}`,
    output_text: '{"answer": "A concise grounded answer.", "sources": ["doc-12#rate-limits"]}',
    feedback: r.rating != null ? [{ rating: r.rating, comment: r.comment, source: "web", created_at: r.created_at }] : [],
    breakdown: { available: true, model: r.model, template_tokens: 180, data_tokens: r.tokens_in - 180, tokens_out: r.tokens_out, template_cost: 0.00003, data_cost: 0.0001, output_cost: 0.00008, total_cost: r.cost, estimated: true },
  };
}

// ---- route table ----
const routes: [RegExp, (m: RegExpMatchArray, q: URLSearchParams, body: any) => any][] = [
  [/^\/api\/health$/, () => ({ status: "ok", version: "0.9.5", db_path: "demo" })],
  [/^\/api\/suites$/, () => SUITES],
  [/^\/api\/suite\/([^/]+)\/runs$/, (m) => suiteRuns(decodeURIComponent(m[1]))],
  [/^\/api\/suite\/([^/]+)\/run\/(\d+)$/, (m) => runDetail(decodeURIComponent(m[1]), +m[2])],
  [/^\/api\/suite\/([^/]+)\/bisect$/, () => ({ found: false, suite: "demo", reason: "No regression in the demo window." })],
  [/^\/api\/prompts$/, () => promptSummaries],
  [/^\/api\/prompts\/search$/, (_m, q) => ({ mode: "semantic", results: promptSummaries.filter((p) => p.name.includes((q.get("q") || "").toLowerCase())).map((p) => ({ name: p.name, score: 0.72, preview: (CONTENT[p.name] || "").slice(0, 120) })) })],
  [/^\/api\/prompts\/near-duplicates$/, () => ({ mode: "semantic", threshold: 0.85, pairs: [{ a: "rag.answer", b: "rag.rerank", similarity: 0.86 }] })],
  [/^\/api\/prompts\/([^/]+)\/content$/, (m, q) => contentResp(decodeURIComponent(m[1]), q.get("v") ? +q.get("v")! : undefined)],
  [/^\/api\/prompts\/([^/]+)\/diff$/, () => ({ additions: 2, deletions: 1, lines: [{ type: "unchanged", old_num: 1, new_num: 1, content: "You are a support assistant for {{product}}." }, { type: "deleted", old_num: 2, new_num: null, content: "Answer briefly." }, { type: "added", old_num: null, new_num: 2, content: "Answer using ONLY the context below." }] })],
  [/^\/api\/prompts\/([^/]+)\/stats$/, (m) => ({ name: decodeURIComponent(m[1]), days: 30, count: 42, metrics: { tokens_in: { min: 600, avg: 1400, p50: 1300, p95: 2600, max: 3000, sum: 58800 }, tokens_out: { min: 80, avg: 220, p50: 200, p95: 480, max: 520, sum: 9240 }, cost: { min: 0.0002, avg: 0.0008, p50: 0.0007, p95: 0.0018, max: 0.002, sum: 0.0336 }, latency_ms: { min: 320, avg: 1400, p50: 1200, p95: 3800, max: 4200, sum: 58800 } }, histogram: Array.from({ length: 8 }, (_, i) => ({ start: 600 + i * 300, end: 900 + i * 300, count: Math.max(1, 8 - Math.abs(i - 3)) })) })],
  [/^\/api\/prompts\/([^/]+)\/runs$/, (m) => ({ runs: suiteRuns("rag-quality").slice(0, 5).map((r) => ({ run_id: r.id, suite_name: "rag-quality", prompt_version: r.prompt_version, model_version: r.model_version, timestamp: r.timestamp, passed: r.overall_pass, score: r.overall_score })) })],
  [/^\/api\/prompts\/([^/]+)\/online-drift$/, (m) => ({ name: decodeURIComponent(m[1]), days: 30, total_calls: 42, drifting_count: 1, status: "drifting", metrics: [{ metric: "cost", label: "cost / call", count: 42, baseline_mean: 0.0006, recent_mean: 0.0009, pct_change: 0.5, slope: 0.00001, p_value: 0.02, direction: "up", bad_direction: "up", drifting: true, severity: "high", message: "cost / call ↑ +50% (p=0.020)" }, { metric: "latency_ms", label: "latency", count: 42, baseline_mean: 1200, recent_mean: 1260, pct_change: 0.05, slope: 1, p_value: 0.3, direction: "up", bad_direction: "up", drifting: false, severity: "none", message: "latency ↑ +5% (p=0.300)" }, { metric: "rating", label: "feedback rating", count: 18, baseline_mean: 0.9, recent_mean: 0.88, pct_change: -0.02, slope: 0, p_value: 0.6, direction: "down", bad_direction: "down", drifting: false, severity: "none", message: "feedback rating ↓ -2% (p=0.600)" }] })],
  [/^\/api\/prompts\/([^/]+)\/examples$/, () => ({ examples: [{ id: 1, prompt_name: "rag.answer", input_text: "What is the rate limit on the free plan?", reference_output: "100 requests per minute.", source_invocation_id: 4990, model: "gpt-4o-mini", created_at: iso(2) }] })],
  [/^\/api\/prompts\/([^/]+)$/, (m) => ({ versions: Array.from({ length: VERSIONS[decodeURIComponent(m[1])] || 1 }, (_, i) => ({ version: (VERSIONS[decodeURIComponent(m[1])] || 1) - i, hash: "demo" + i, created_at: iso(i * 4 + 1), tags: i === 0 ? (TAGS[decodeURIComponent(m[1])] || []) : [] })) })],
  [/^\/api\/models\/([^/]+)\/compare$/, () => ({ suite_name: "rag-quality", baseline: { model_version: "gpt-4o-mini", run_count: 8, overall_mean: 0.9, overall_std: 0.02 }, candidate: { model_version: "claude-haiku-4-5", run_count: 6, overall_mean: 0.92, overall_std: 0.018 }, overall_delta: 0.02, percentile: 84, assertion_comparisons: [], cost_ratio: 1.6, score_per_dollar_baseline: 1100, score_per_dollar_candidate: 720, verdict: "comparable", verdict_reason: "Within noise; keep the cheaper baseline." })],
  [/^\/api\/models\/([^/]+)$/, () => ({ versions: [{ model_version: "gpt-4o-mini", run_count: 8 }, { model_version: "claude-haiku-4-5", run_count: 6 }] })],
  [/^\/api\/cost\/coverage$/, () => ({ days: 14, models_seen: 3, uncosted: [], uncosted_calls: 0 })],
  [/^\/api\/cost$/, (_m, q) => costResp(+(q.get("days") || 14), q.get("name"))],
  [/^\/api\/config$/, () => CONFIG],
  [/^\/api\/budgets$/, () => ({ budgets: [{ id: 1, scope: "global", target: null, period: "monthly", limit_usd: 50, spend: 12.4, pct: 0.248, breached: false }, { id: 2, scope: "module", target: "rag", period: "daily", limit_usd: 2, spend: 0.7, pct: 0.35, breached: false }] })],
  [/^\/api\/invocations\/(\d+)\/scan$/, (m) => (+m[1] % 3 === 0 ? { input: [{ type: "email", category: "pii", severity: "medium", count: 1, sample: "••••••@acme.com" }], output: [], total: 1, has_secret: false, worst_severity: "medium" } : { input: [], output: [], total: 0, has_secret: false, worst_severity: null })],
  [/^\/api\/invocations\/(\d+)$/, (m) => invocationDetail(+m[1])],
  [/^\/api\/invocations$/, (_m, q) => { let r = [...INVOCATIONS]; const name = q.get("name"); if (name) r = r.filter((x) => x.prompt_name === name); if (q.get("order") === "cost") r.sort((a, b) => (b.cost || 0) - (a.cost || 0)); return { invocations: r.slice(0, +(q.get("limit") || 100)) }; }],
];

// POST endpoints → plausible canned responses (so demo interactions don't error).
function postResponse(path: string, body: any): any {
  if (path.includes("/playground/model")) return { response: '{"answer": "A grounded demo answer.", "sources": ["doc-7#pricing"]}', latency_ms: 920, tokens_in: 740, tokens_out: 96, cost: 0.00012 };
  if (path.includes("/playground/eval")) return { overall_passed: true, overall_score: 0.86, passed_count: 3, total_count: 4, results: [] };
  if (path.includes("/examples/run")) return { prompt_name: "rag.answer", model: body?.model || "gpt-4o-mini", threshold: 0.8, mode: "semantic", count: 1, passed: 1, accuracy: 1, results: [{ id: 1, score: 0.91, passed: true, output_preview: "100 requests per minute.", reference_preview: "100 requests per minute.", latency_ms: 800, error: null }] };
  if (path.includes("/lint")) return { variables: [], lint: [] };
  return { ok: true };
}

function match(path: string, method: string, body: any): any {
  const url = new URL(path, "http://demo.local");
  const p = url.pathname;
  if (method === "POST" || method === "DELETE") return postResponse(p, body);
  for (const [re, fn] of routes) {
    const m = p.match(re);
    if (m) return fn(m, url.searchParams, body);
  }
  return {};
}

export function installDemoFetch() {
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : (input as Request).url;
    if (url.includes("/api/")) {
      const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
      let body: any = undefined;
      try { body = init?.body ? JSON.parse(init.body as string) : undefined; } catch { /* ignore */ }
      const path = url.slice(url.indexOf("/api/"));
      const data = match(path, method, body);
      await new Promise((r) => setTimeout(r, 120)); // a touch of latency so loading states show
      return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return orig(input, init);
  };
}
