/**
 * Demo mode: a fetch interceptor that serves synthetic data for /api/* so the
 * REAL dashboard runs as a static, backend-less demo (GitHub Pages). Nothing in
 * the app code changes — installDemoFetch() monkeypatches window.fetch when the
 * build flag VITE_DEMO is set, so the demo can never drift from the product.
 *
 * The dataset below is a fictional "Helpdesk AI" product: a RAG support
 * assistant, a triage classifier, an agent, and summarizers — enough breadth
 * that every dashboard surface (cost drill-down, drift, traces, PII, evals,
 * golden sets, playground) has something real to show.
 */

const now = Date.now();
const iso = (daysAgo: number, h = 0) => new Date(now - daysAgo * 864e5 - h * 36e5).toISOString();
const r2 = (n: number, d = 4) => Number(n.toFixed(d));
// deterministic pseudo-random in [0,1) so the demo looks the same every load
const rnd = (seed: number) => { const x = Math.sin(seed * 12.9898) * 43758.5453; return x - Math.floor(x); };
const moduleOf = (n: string) => (n.includes(".") ? n.split(".")[0] : "other");

// ---- prompts: 12 across 7 modules, all {{var}} syntax (shows live highlighting) ----
const CONTENT: Record<string, string> = {
  "rag.answer": `You are the support assistant for {{product}}.
Answer the question using ONLY the context below. If it isn't supported, say "I don't know" — never guess.

Context:
{{context}}

Question: {{question}}
Return JSON: {"answer": "...", "sources": ["..."], "confidence": 0.0-1.0}`,
  "rag.rerank": `Rank the {{k}} candidate passages by how well they answer: "{{query}}".
Return a JSON array of passage ids, most relevant first. Drop anything off-topic.`,
  "rag.query_expansion": `Rewrite the user query into {{n}} diverse search queries that cover synonyms and sub-questions.
Original: {{query}}
Return a JSON array of strings.`,
  "agent.planner": `You are the planner for a {{role}} agent.
Goal: {{goal}}
Tools available: {{tools}}
Recent steps: {{history}}
Think step by step, then emit the next action as JSON: {"tool": "...", "args": {...}}.`,
  "agent.tool_select": `Pick the single best tool for the task from: {{tools}}.
Task: {{task}}
Reply with the tool name only.`,
  "support.classify": `Classify the customer message into exactly one of: billing, bug, feature_request, account, how_to, other.
Message: {{message}}
Reply with JSON {"intent": "...", "confidence": 0.0-1.0}.`,
  "support.reply": `You are a {{tone}} support agent for {{product}}.
Draft a reply to the customer using the resolution notes. Cite the relevant policy.
Customer: {{message}}
Resolution notes: {{notes}}`,
  "summary.tldr": `Summarize the document into {{n}} crisp bullet points for an executive. Lead with the decision or risk.

{{document}}`,
  "summary.section": `Write a {{length}}-word summary of the "{{section}}" section below for a {{audience}} reader.

{{content}}`,
  "safety.guardrail": `Decide whether to answer the request for {{product}}.
Refuse anything unsafe, out-of-scope, or asking to bypass policy.
Request: {{request}}
Return JSON: {"allowed": true|false, "reason": "..."}`,
  "intent.router": `Route the message to one of: {{handlers}}.
Message: {{message}}
Return the handler name.`,
  "chat.system": `You are {{assistant_name}}, the assistant for {{product}}. Be concise, accurate, and cite sources. Today is {{date}}.`,
};
const VERSIONS: Record<string, number> = {
  "rag.answer": 4, "support.classify": 3, "agent.planner": 2, "support.reply": 2, "summary.section": 2,
};
const TAGS: Record<string, string[]> = {
  "rag.answer": ["prod"], "support.classify": ["prod"], "safety.guardrail": ["prod"], "agent.planner": ["staging"],
};
const NAMES = Object.keys(CONTENT);
const ver = (n: string) => VERSIONS[n] ?? 1;
const promptSummaries = NAMES.map((name) => ({ name, latest_version: ver(name), tags: TAGS[name] || [] }));

function vars(name: string): string[] {
  return [...(CONTENT[name] || "").matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]);
}
function lintFor(name: string) {
  const out: { level: string; message: string }[] = [];
  if (/Return JSON|JSON:/.test(CONTENT[name] || "")) out.push({ level: "info", message: "Good: output format is specified (JSON)." });
  if (name === "agent.planner") out.push({ level: "warning", message: "5 variables — consider whether {{history}} can grow unbounded and blow the token budget." });
  return out;
}
function contentResp(name: string, v?: number) {
  return {
    name, version: v ?? ver(name), content: CONTENT[name] || "",
    hash: "demo-" + name + "-" + (v ?? ver(name)), created_at: iso((ver(name) - (v ?? ver(name))) * 4 + 1),
    tags: (v ?? ver(name)) === ver(name) ? (TAGS[name] || []) : [],
    variables: vars(name), lint: lintFor(name),
  };
}

// ---- suites: 5, varied states, real run histories ----
interface Suite { name: string; latest_score: number; passed: boolean; model_version: string; prompt: string; drift_status: "stable" | "drifting"; drift_slope: number; spark: number[]; }
const SUITES_RAW: Suite[] = [
  { name: "rag-quality", latest_score: 0.913, passed: true, model_version: "gpt-4o-mini", prompt: "rag.answer", drift_status: "stable", drift_slope: 0.003, spark: [0.88, 0.9, 0.89, 0.91, 0.9, 0.92, 0.913] },
  { name: "support-routing", latest_score: 0.957, passed: true, model_version: "gpt-4o-mini", prompt: "support.classify", drift_status: "stable", drift_slope: -0.001, spark: [0.95, 0.96, 0.95, 0.96, 0.955, 0.96, 0.957] },
  { name: "summary-faithfulness", latest_score: 0.842, passed: false, model_version: "claude-haiku-4-5", prompt: "summary.tldr", drift_status: "drifting", drift_slope: -0.041, spark: [0.91, 0.9, 0.88, 0.87, 0.86, 0.85, 0.842] },
  { name: "agent-trajectory", latest_score: 0.889, passed: true, model_version: "gpt-4o", prompt: "agent.planner", drift_status: "stable", drift_slope: 0.006, spark: [0.85, 0.86, 0.87, 0.88, 0.88, 0.89, 0.889] },
  { name: "safety-redteam", latest_score: 0.968, passed: true, model_version: "gpt-4o-mini", prompt: "safety.guardrail", drift_status: "stable", drift_slope: 0.0, spark: [0.96, 0.97, 0.96, 0.97, 0.965, 0.97, 0.968] },
];
const SUITES = SUITES_RAW.map((s) => ({
  name: s.name, latest_score: s.latest_score, passed: s.passed, model_version: s.model_version,
  prompt_version: ver(s.prompt), timestamp: iso(0, Math.round(rnd(s.name.length) * 8)),
  drift_status: s.drift_status, drift_slope: s.drift_slope, sparkline_scores: s.spark,
}));
const suiteByName = (n: string) => SUITES_RAW.find((s) => s.name === n) || SUITES_RAW[0];

function suiteRuns(name: string) {
  const s = suiteByName(name);
  const N = 16;
  return Array.from({ length: N }, (_, i) => {
    const drift = s.drift_status === "drifting" ? (N - 1 - i) * 0.004 : 0;
    const noise = (rnd(name.length + i) - 0.5) * 0.025;
    const score = Math.max(0.6, Math.min(0.99, s.latest_score + drift + noise - (i === 0 ? 0 : 0)));
    return {
      id: 1000 + name.length * 100 + i, suite_name: name, prompt_name: s.prompt,
      prompt_version: Math.max(1, ver(s.prompt) - (i % 3)),
      model_version: i % 5 === 0 ? "gpt-4o" : s.model_version,
      timestamp: iso(i * 2, 3), overall_pass: score >= 0.85, overall_score: r2(score, 3),
    };
  });
}

const ASSERTS = ["semantic", "contains", "grounded", "json_valid", "not_contains"];
function runDetail(name: string, runId: number) {
  const s = suiteByName(name);
  const failing = s.name === "summary-faithfulness";
  const assertions = ASSERTS.map((t, i) => ({
    id: runId * 10 + i, run_id: runId, test_name: `case_${i + 1}_${["retrieval", "format", "grounding", "schema", "safety"][i]}`,
    assertion_type: t,
    passed: !(failing && t === "grounded"),
    score: t === "json_valid" ? 1 : r2(failing && t === "grounded" ? 0.62 : 0.8 + i * 0.04, 2),
    details: t === "grounded"
      ? { judge_model: "gpt-4o-mini", judge_tokens_in: 280 + i * 40, judge_tokens_out: 14, judge_cost: 0.00006, judge_cost_estimated: true, reason: failing ? "1 of 4 claims unsupported by the source" : "all claims grounded" }
      : { criteria: `${t} check on the response` },
    latency_ms: 380 + i * 140,
  }));
  const jc = assertions.find((a) => a.assertion_type === "grounded")!.details as any;
  return {
    run: { id: runId, suite_name: name, prompt_name: s.prompt, prompt_version: ver(s.prompt), model_version: s.model_version, timestamp: iso(0, 3), overall_pass: !failing, overall_score: s.latest_score },
    assertions,
    judge: { calls: 1, model: "gpt-4o-mini", tokens_in: jc.judge_tokens_in, tokens_out: jc.judge_tokens_out, cost: jc.judge_cost, estimated: true, unpriced: false },
  };
}

// ---- invocations + cost (the per-call ledger) ----
const MODELS = ["gpt-4o-mini", "gpt-4o", "claude-haiku-4-5", "llama-3.1-8b"];
const RATE: Record<string, [number, number] | null> = {
  "gpt-4o-mini": [0.15, 0.6], "gpt-4o": [2.5, 10], "claude-haiku-4-5": [0.25, 1.25], "llama-3.1-8b": null, // unpriced -> coverage gap
};
// weight prompts so the cost-by-module chart looks realistic
const WEIGHTED = [
  "rag.answer", "rag.answer", "rag.answer", "rag.answer", "support.classify", "support.classify", "support.classify",
  "support.reply", "support.reply", "rag.rerank", "rag.rerank", "rag.query_expansion", "summary.tldr", "summary.tldr",
  "summary.section", "agent.planner", "agent.tool_select", "safety.guardrail", "intent.router", "chat.system",
];
const previewFor = (name: string, id: number) => {
  const m = moduleOf(name);
  if (m === "summary") return "• Renewal at risk: usage down 18% QoQ. • Two P1 bugs open >30d. • Expansion blocked on SSO.";
  if (name === "support.reply") return "Hi Dana — thanks for reaching out. Your plan does include priority support, so I've…";
  if (m === "agent") return '{"tool": "search_docs", "args": {"q": "rate limit free tier"}}';
  if (name === "safety.guardrail") return '{"allowed": false, "reason": "asks to disable content filtering"}';
  if (name === "intent.router") return "billing";
  return '{"answer": "100 requests per minute on the free plan.", "sources": ["billing.md#limits"], "confidence": 0.9}';
};
const INVOCATIONS = Array.from({ length: 210 }, (_, i) => {
  const name = WEIGHTED[Math.floor(rnd(i + 1) * WEIGHTED.length)];
  const model = i % 11 === 0 ? "llama-3.1-8b" : i % 7 === 0 ? "gpt-4o" : i % 3 === 0 ? "claude-haiku-4-5" : "gpt-4o-mini";
  const tin = 400 + Math.floor(rnd(i + 7) * 3200);
  const tout = 40 + Math.floor(rnd(i + 13) * 600);
  const rate = RATE[model];
  const cost = rate ? (tin * rate[0] + tout * rate[1]) / 1e6 : 0;
  const dayAgo = Math.floor(rnd(i + 3) * 30);
  const rated = i % 3 === 0;
  const low = rated && rnd(i + 5) < 0.22;
  return {
    id: 9000 - i, prompt_name: name, prompt_version: Math.max(1, ver(name) - (i % 2)),
    created_at: iso(dayAgo, Math.floor(rnd(i + 17) * 24)),
    model, tokens_in: tin, tokens_out: tout, cost: r2(cost, 6), latency_ms: 280 + Math.floor(rnd(i + 19) * 4200),
    has_capture: i % 3 === 0,
    output_preview: previewFor(name, i),
    rating: rated ? (low ? 0.4 : 0.95) : null,
    comment: low ? pick3(i, ["missed the figure", "wrong policy cited", "hallucinated a source"]) : null,
  };
});
function pick3<T>(i: number, a: T[]): T { return a[i % a.length]; }

function costResp(days: number, name?: string | null, model?: string | null) {
  const cutoff = now - days * 864e5;
  let rows = INVOCATIONS.filter((r) => new Date(r.created_at).getTime() >= cutoff);
  if (name) rows = rows.filter((r) => r.prompt_name === name || moduleOf(r.prompt_name) === name);
  if (model) rows = rows.filter((r) => r.model === model);
  const byName: Record<string, any> = {};
  for (const r of rows) {
    const e = (byName[r.prompt_name] ||= { name: r.prompt_name, calls: 0, tokens_in: 0, tokens_out: 0, cost: 0, models: new Set<string>() });
    e.calls++; e.tokens_in += r.tokens_in; e.tokens_out += r.tokens_out; e.cost += r.cost || 0; e.models.add(r.model);
  }
  const byDate: Record<string, any> = {};
  for (const r of rows) {
    const d = r.created_at.slice(0, 10);
    const e = (byDate[d] ||= { date: d, calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 });
    e.calls++; e.tokens_in += r.tokens_in; e.tokens_out += r.tokens_out; e.cost += r.cost || 0;
  }
  const total_cost = rows.reduce((a, r) => a + (r.cost || 0), 0);
  return {
    summary: {
      total_cost: r2(total_cost), total_calls: rows.length,
      total_tokens_in: rows.reduce((a, r) => a + r.tokens_in, 0),
      total_tokens_out: rows.reduce((a, r) => a + r.tokens_out, 0),
      cache_hit_rate: 0.34, cache_savings: r2(total_cost * 0.21), avg_cost: rows.length ? total_cost / rows.length : 0,
    },
    by_name: Object.values(byName).map((e: any) => ({ ...e, cost: r2(e.cost, 5), models: [...e.models] })).sort((a, b) => b.cost - a.cost),
    by_date: Object.values(byDate).map((e: any) => ({ ...e, cost: r2(e.cost, 5) })).sort((a, b) => a.date.localeCompare(b.date)),
  };
}

const CONFIG = {
  models: [
    { id: "gpt-4o-mini", provider: "openai", label: "GPT-4o mini" },
    { id: "gpt-4o", provider: "openai", label: "GPT-4o" },
    { id: "claude-haiku-4-5", provider: "anthropic", label: "Claude Haiku 4.5" },
    { id: "claude-sonnet-4-5", provider: "anthropic", label: "Claude Sonnet 4.5" },
  ],
  judge: { model: "gpt-4o-mini" },
  dashboard: { default_days: 14 },
  pricing: {},
  key_status: { openai: true, anthropic: true, xai: false, google: false, azure: false },
  path: "~/helpdesk-ai/.promptry/config.toml",
};

// captured request/response, with a couple traces carrying PII/secrets to demo the scanner
function captured(name: string, id: number) {
  const sys = (CONTENT[name] || "").split("\n").slice(0, 3).join("\n");
  let user = "How do I increase the rate limit on the free plan?";
  let out = previewFor(name, id);
  if (id % 9 === 0) { user = "My account is jordan.lee@northwind.io — why was I double charged?"; }
  if (id % 23 === 0) { out = 'Use the admin key sk-live-9f2ab7c41de83b6072aa to rotate it via the API.'; }
  return { input_text: `System:\n${sys}\n\nUser: ${user}`, output_text: out };
}
function invocationDetail(id: number) {
  const r = INVOCATIONS.find((x) => x.id === id) || INVOCATIONS[0];
  const cap = captured(r.prompt_name, id);
  const tmplTok = Math.round(r.tokens_in * 0.55);
  return {
    id: r.id, prompt_name: r.prompt_name, prompt_version: r.prompt_version, created_at: r.created_at,
    request_id: "req-" + r.id, metadata: { model: r.model, tokens_in: r.tokens_in, tokens_out: r.tokens_out, cost: r.cost, latency_ms: r.latency_ms },
    input_text: cap.input_text, output_text: cap.output_text,
    feedback: r.rating != null ? [{ rating: r.rating, comment: r.comment, source: "web-widget", created_at: r.created_at }] : [],
    breakdown: r.cost
      ? { available: true, model: r.model, template_tokens: tmplTok, data_tokens: r.tokens_in - tmplTok, tokens_out: r.tokens_out,
          template_cost: r2((tmplTok * (RATE[r.model]?.[0] || 0)) / 1e6, 6), data_cost: r2(((r.tokens_in - tmplTok) * (RATE[r.model]?.[0] || 0)) / 1e6, 6),
          output_cost: r2((r.tokens_out * (RATE[r.model]?.[1] || 0)) / 1e6, 6), total_cost: r.cost, estimated: true }
      : { available: false, reason: "Model 'llama-3.1-8b' has no pricing entry — add it under [pricing] to see the split." },
  };
}
function scanResp(id: number) {
  const out: any = { input: [], output: [], total: 0, has_secret: false, worst_severity: null };
  if (id % 9 === 0) out.input.push({ type: "email", category: "pii", severity: "medium", count: 1, sample: "••••••••northwind.io" });
  if (id % 23 === 0) { out.output.push({ type: "openai_api_key", category: "secret", severity: "high", count: 1, sample: "••••••••72aa" }); out.has_secret = true; }
  out.total = out.input.length + out.output.length;
  out.worst_severity = out.has_secret ? "high" : out.total ? "medium" : null;
  return out;
}

// golden eval-from-trace examples, a few per prompt
const GOLDEN: Record<string, any[]> = {
  "rag.answer": [
    { id: 1, prompt_name: "rag.answer", input_text: "What is the rate limit on the free plan?", reference_output: '{"answer":"100 requests per minute","sources":["billing.md#limits"]}', source_invocation_id: 8990, model: "gpt-4o-mini", created_at: iso(2) },
    { id: 2, prompt_name: "rag.answer", input_text: "Do you offer SSO on the Team plan?", reference_output: '{"answer":"SSO is available on Team and Enterprise","sources":["security.md#sso"]}', source_invocation_id: 8975, model: "gpt-4o-mini", created_at: iso(5) },
    { id: 3, prompt_name: "rag.answer", input_text: "Can I export my data?", reference_output: '{"answer":"Yes, via Settings → Export (CSV/JSON)","sources":["data.md#export"]}', source_invocation_id: 8961, model: "gpt-4o", created_at: iso(8) },
  ],
  "support.classify": [
    { id: 4, prompt_name: "support.classify", input_text: "I was charged twice this month", reference_output: '{"intent":"billing","confidence":0.97}', source_invocation_id: 8950, model: "gpt-4o-mini", created_at: iso(3) },
    { id: 5, prompt_name: "support.classify", input_text: "The export button does nothing", reference_output: '{"intent":"bug","confidence":0.92}', source_invocation_id: 8944, model: "gpt-4o-mini", created_at: iso(6) },
  ],
};

// ---- route table ----
const routes: [RegExp, (m: RegExpMatchArray, q: URLSearchParams, body: any) => any][] = [
  [/^\/api\/health$/, () => ({ status: "ok", version: "1.0.0", db_path: "helpdesk-ai.db" })],
  [/^\/api\/suites$/, () => SUITES],
  [/^\/api\/suite\/([^/]+)\/runs$/, (m) => suiteRuns(decodeURIComponent(m[1]))],
  [/^\/api\/suite\/([^/]+)\/run\/(\d+)$/, (m) => runDetail(decodeURIComponent(m[1]), +m[2])],
  [/^\/api\/suite\/([^/]+)\/bisect$/, (m) => {
    const name = decodeURIComponent(m[1]);
    if (suiteByName(name).drift_status !== "drifting") return { found: false, suite: name, reason: "No passing→failing transition in the window." };
    return { found: true, suite: name, prompt_changed: true, model_changed: false,
      last_good: { run_id: 1305, prompt_version: 1, model_version: "claude-haiku-4-5", timestamp: iso(10), score: 0.9 },
      first_bad: { run_id: 1306, prompt_version: 1, model_version: "claude-haiku-4-5", timestamp: iso(8), score: 0.86 } };
  }],
  [/^\/api\/prompts$/, () => promptSummaries],
  [/^\/api\/prompts\/search$/, (_m, q) => {
    const query = (q.get("q") || "").toLowerCase();
    const hits = NAMES.filter((n) => n.includes(query) || (CONTENT[n] || "").toLowerCase().includes(query));
    return { mode: "semantic", results: hits.slice(0, 10).map((n, i) => ({ name: n, score: r2(0.82 - i * 0.05, 3), preview: (CONTENT[n] || "").replace(/\n/g, " ").slice(0, 140) })) };
  }],
  [/^\/api\/prompts\/near-duplicates$/, () => ({ mode: "semantic", threshold: 0.85, pairs: [
    { a: "summary.tldr", b: "summary.section", similarity: 0.88 },
    { a: "intent.router", b: "support.classify", similarity: 0.86 },
  ] })],
  [/^\/api\/prompts\/([^/]+)\/content$/, (m, q) => contentResp(decodeURIComponent(m[1]), q.get("v") ? +q.get("v")! : undefined)],
  [/^\/api\/prompts\/([^/]+)\/diff$/, (m) => {
    const name = decodeURIComponent(m[1]);
    const first = (CONTENT[name] || "You are an assistant.").split("\n")[0];
    return { additions: 2, deletions: 1, lines: [
      { type: "unchanged", old_num: 1, new_num: 1, content: first },
      { type: "deleted", old_num: 2, new_num: null, content: "Answer briefly." },
      { type: "added", old_num: null, new_num: 2, content: 'If it isn\'t supported, say "I don\'t know" — never guess.' },
      { type: "added", old_num: null, new_num: 3, content: 'Return JSON: {"answer": "...", "confidence": 0.0-1.0}' },
    ] };
  }],
  [/^\/api\/prompts\/([^/]+)\/stats$/, (m) => {
    const name = decodeURIComponent(m[1]);
    const rows = INVOCATIONS.filter((r) => r.prompt_name === name);
    const col = (key: "tokens_in" | "tokens_out" | "cost" | "latency_ms") => {
      const vs = rows.map((r: any) => r[key] || 0).sort((a, b) => a - b);
      if (!vs.length) return { min: 0, avg: 0, p50: 0, p95: 0, max: 0, sum: 0 };
      const at = (p: number) => vs[Math.min(vs.length - 1, Math.floor(p * (vs.length - 1)))];
      const sum = vs.reduce((a, b) => a + b, 0);
      return { min: r2(vs[0], 5), avg: r2(sum / vs.length, 5), p50: r2(at(0.5), 5), p95: r2(at(0.95), 5), max: r2(vs[vs.length - 1], 5), sum: r2(sum, 5) };
    };
    const tin = rows.map((r) => r.tokens_in).sort((a, b) => a - b);
    const lo = tin[0] || 400, hi = tin[tin.length - 1] || 3000, w = (hi - lo) / 8 || 1;
    const hist = Array.from({ length: 8 }, (_, i) => ({ start: Math.round(lo + i * w), end: Math.round(lo + (i + 1) * w), count: tin.filter((t) => t >= lo + i * w && t < lo + (i + 1) * w).length }));
    return { name, days: 30, count: rows.length, metrics: { tokens_in: col("tokens_in"), tokens_out: col("tokens_out"), cost: col("cost"), latency_ms: col("latency_ms") }, histogram: hist };
  }],
  [/^\/api\/prompts\/([^/]+)\/runs$/, (m) => {
    const name = decodeURIComponent(m[1]);
    const s = SUITES_RAW.find((x) => x.prompt === name);
    if (!s) return { runs: [] };
    return { runs: suiteRuns(s.name).slice(0, 6).map((r) => ({ run_id: r.id, suite_name: s.name, prompt_version: r.prompt_version, model_version: r.model_version, timestamp: r.timestamp, passed: r.overall_pass, score: r.overall_score })) };
  }],
  [/^\/api\/prompts\/([^/]+)\/online-drift$/, (m) => {
    const name = decodeURIComponent(m[1]);
    const drifting = name === "rag.answer" || name === "summary.tldr";
    return { name, days: 30, total_calls: INVOCATIONS.filter((r) => r.prompt_name === name).length || 40, drifting_count: drifting ? 2 : 0, status: drifting ? "drifting" : "stable", metrics: [
      { metric: "cost", label: "cost / call", count: 40, baseline_mean: 0.00061, recent_mean: drifting ? 0.00094 : 0.00063, pct_change: drifting ? 0.54 : 0.03, slope: 0.00001, p_value: drifting ? 0.018 : 0.4, direction: "up", bad_direction: "up", drifting: drifting, severity: drifting ? "high" : "none", message: drifting ? "cost / call ↑ +54% (p=0.018)" : "cost / call → +3% (p=0.400)" },
      { metric: "rating", label: "feedback rating", count: 22, baseline_mean: 0.91, recent_mean: drifting ? 0.79 : 0.9, pct_change: drifting ? -0.13 : -0.01, slope: 0, p_value: drifting ? 0.03 : 0.7, direction: "down", bad_direction: "down", drifting: drifting, severity: drifting ? "medium" : "none", message: drifting ? "feedback rating ↓ -13% (p=0.030)" : "feedback rating → -1% (p=0.700)" },
      { metric: "latency_ms", label: "latency", count: 40, baseline_mean: 1180, recent_mean: 1240, pct_change: 0.05, slope: 2, p_value: 0.32, direction: "up", bad_direction: "up", drifting: false, severity: "none", message: "latency ↑ +5% (p=0.320)" },
      { metric: "tokens_out", label: "output tokens", count: 40, baseline_mean: 210, recent_mean: 224, pct_change: 0.07, slope: 0.5, p_value: 0.28, direction: "up", bad_direction: "up", drifting: false, severity: "none", message: "output tokens ↑ +7% (p=0.280)" },
    ] };
  }],
  [/^\/api\/prompts\/([^/]+)\/examples$/, (m) => ({ examples: GOLDEN[decodeURIComponent(m[1])] || [] })],
  [/^\/api\/prompts\/([^/]+)$/, (m) => {
    const name = decodeURIComponent(m[1]); const n = ver(name);
    return { versions: Array.from({ length: n }, (_, i) => ({ version: n - i, hash: "demo-" + name + "-" + (n - i), created_at: iso(i * 4 + 1), tags: i === 0 ? (TAGS[name] || []) : [] })) };
  }],
  [/^\/api\/models\/([^/]+)\/compare$/, (m) => ({
    suite_name: decodeURIComponent(m[1]),
    baseline: { model_version: "gpt-4o-mini", run_count: 9, overall_mean: 0.901, overall_std: 0.021 },
    candidate: { model_version: "claude-haiku-4-5", run_count: 7, overall_mean: 0.924, overall_std: 0.018 },
    overall_delta: 0.023, percentile: 86,
    assertion_comparisons: [
      { assertion_type: "semantic", baseline_mean: 0.88, baseline_std: 0.03, candidate_score: 0.92, delta: 0.04, verdict: "better" },
      { assertion_type: "grounded", baseline_mean: 0.91, baseline_std: 0.02, candidate_score: 0.93, delta: 0.02, verdict: "comparable" },
      { assertion_type: "json_valid", baseline_mean: 1.0, baseline_std: 0.0, candidate_score: 0.98, delta: -0.02, verdict: "worse" },
    ],
    cost_ratio: 1.7, score_per_dollar_baseline: 1180, score_per_dollar_candidate: 712,
    verdict: "keep_baseline", verdict_reason: "Claude scores +2.3pts but costs 1.7× — gpt-4o-mini wins on score-per-dollar.",
  })],
  [/^\/api\/models\/([^/]+)$/, () => ({ versions: [{ model_version: "gpt-4o-mini", run_count: 9 }, { model_version: "claude-haiku-4-5", run_count: 7 }, { model_version: "gpt-4o", run_count: 4 }] })],
  [/^\/api\/cost\/coverage$/, (_m, q) => ({ days: +(q.get("days") || 14), models_seen: 4, uncosted: [{ model: "llama-3.1-8b", calls: INVOCATIONS.filter((r) => r.model === "llama-3.1-8b").length }], uncosted_calls: INVOCATIONS.filter((r) => r.model === "llama-3.1-8b").length })],
  [/^\/api\/cost$/, (_m, q) => costResp(+(q.get("days") || 14), q.get("name"), q.get("model"))],
  [/^\/api\/config$/, () => CONFIG],
  [/^\/api\/budgets$/, () => ({ budgets: [
    { id: 1, scope: "global", target: null, period: "monthly", limit_usd: 50, spend: 21.4, pct: 0.428, breached: false },
    { id: 2, scope: "module", target: "rag", period: "daily", limit_usd: 1.5, spend: 1.31, pct: 0.873, breached: false },
    { id: 3, scope: "prompt", target: "agent.planner", period: "monthly", limit_usd: 5, spend: 5.6, pct: 1.12, breached: true },
  ] })],
  [/^\/api\/invocations\/(\d+)\/scan$/, (m) => scanResp(+m[1])],
  [/^\/api\/invocations\/(\d+)$/, (m) => invocationDetail(+m[1])],
  [/^\/api\/invocations$/, (_m, q) => {
    let rows = [...INVOCATIONS];
    const name = q.get("name"); if (name) rows = rows.filter((r) => r.prompt_name === name);
    if (q.get("captured_only") === "true") rows = rows.filter((r) => r.has_capture);
    const minR = q.get("min_rating"); if (minR != null) rows = rows.filter((r) => r.rating != null && r.rating <= +minR);
    rows.sort((a, b) => q.get("order") === "cost" ? (b.cost || 0) - (a.cost || 0) : new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    return { invocations: rows.slice(0, +(q.get("limit") || 100)) };
  }],
];

// POST/DELETE → plausible canned responses so demo interactions never error.
function postResponse(path: string, body: any): any {
  if (path.includes("/playground/model")) {
    const model = body?.model || "gpt-4o-mini";
    const tin = 620 + Math.floor(rnd(model.length) * 400), tout = 80 + Math.floor(rnd(model.length + 1) * 120);
    const rate = RATE[model] || [0.15, 0.6];
    return { response: '{"answer": "The free plan allows 100 requests per minute (10k/day). Bursts over that return HTTP 429.", "sources": ["billing.md#limits"], "confidence": 0.93}', latency_ms: 700 + Math.floor(rnd(model.length + 2) * 900), tokens_in: tin, tokens_out: tout, cost: r2((tin * rate[0] + tout * rate[1]) / 1e6, 6) };
  }
  if (path.includes("/playground/eval")) return { overall_passed: true, overall_score: 0.88, passed_count: 3, total_count: 4, results: [
    { index: 0, type: "contains", passed: true, score: 1, details: { found: ["100 requests"] } },
    { index: 1, type: "json_valid", passed: true, score: 1, details: {} },
    { index: 2, type: "not_contains", passed: true, score: 1, details: {} },
    { index: 3, type: "max_tokens", passed: false, score: 0.5, details: { tokens: 142, limit: 120 } },
  ] };
  if (path.includes("/examples/run")) return { prompt_name: "rag.answer", model: body?.model || "gpt-4o-mini", threshold: body?.threshold ?? 0.8, mode: "semantic", count: 3, passed: 2, accuracy: 0.667, results: [
    { id: 1, score: 0.94, passed: true, output_preview: "100 requests per minute.", reference_preview: "100 requests per minute", latency_ms: 760, error: null },
    { id: 2, score: 0.88, passed: true, output_preview: "SSO is on Team and Enterprise.", reference_preview: "SSO is available on Team and Enterprise", latency_ms: 810, error: null },
    { id: 3, score: 0.61, passed: false, output_preview: "You can export from the dashboard.", reference_preview: "Yes, via Settings → Export (CSV/JSON)", latency_ms: 690, error: null },
  ] };
  if (path.includes("/lint")) {
    const content = body?.content || "";
    const v = [...content.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((x: any) => x[1]);
    return { variables: [...new Set(v)], lint: /JSON/.test(content) ? [{ level: "info", message: "Output format specified." }] : [] };
  }
  return { ok: true };
}

function match(path: string, method: string, body: any): any {
  const url = new URL(path, "http://demo.local");
  const p = url.pathname;
  if (method === "POST" || method === "DELETE") return postResponse(p, body);
  for (const [re, fn] of routes) {
    const mm = p.match(re);
    if (mm) return fn(mm, url.searchParams, body);
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
      await new Promise((res) => setTimeout(res, 90)); // a touch of latency so loading states show
      return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return orig(input, init);
  };
}
