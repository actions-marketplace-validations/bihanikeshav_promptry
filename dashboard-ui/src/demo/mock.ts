/**
 * Demo mode: a fetch interceptor that serves synthetic data for /api/* so the
 * REAL dashboard runs as a static, backend-less demo (GitHub Pages). Nothing in
 * the app code changes — installDemoFetch() monkeypatches window.fetch when the
 * build flag VITE_DEMO is set, so the demo can never drift from the product.
 *
 * Fictional "Helpdesk AI" product at production scale: ~1.6M calls / month
 * across a RAG assistant, a triage classifier, an agent, summarizers, and a
 * safety guardrail. Cost rollups are curated aggregates (a real deployment's
 * numbers); the trace list materializes a recent sample.
 */

// The real fetch, captured before installDemoFetch() overrides window.fetch,
// so the demo can make genuine cross-origin calls (e.g. the GitHub API for the
// live version) instead of hardcoding.
const realFetch: typeof fetch =
  typeof window !== "undefined" ? window.fetch.bind(window) : fetch;

// Latest published version, read live from the GitHub releases API so the demo
// never carries a stale hardcoded version. Cached for the page's lifetime.
let _versionPromise: Promise<string> | null = null;
function liveVersion(): Promise<string> {
  if (!_versionPromise) {
    _versionPromise = realFetch("https://api.github.com/repos/bihanikeshav/promptry/releases/latest")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => String(j.tag_name || "").replace(/^v/, ""))
      .catch(() => ""); // offline / rate-limited: show no version rather than a wrong one
  }
  return _versionPromise;
}

const now = Date.now();
const iso = (daysAgo: number, h = 0) => new Date(now - daysAgo * 864e5 - h * 36e5).toISOString();
const r2 = (n: number, d = 4) => Number(n.toFixed(d));
const rnd = (seed: number) => { const x = Math.sin(seed * 12.9898) * 43758.5453; return x - Math.floor(x); };
const moduleOf = (n: string) => (n.includes(".") ? n.split(".")[0] : "other");

// ---- prompts: 12 across 7 modules, all {{var}} syntax (shows live highlighting) ----
const CONTENT: Record<string, string> = {
  "rag.answer": `Question: {{question}}

Retrieved context:
{{context}}

You are a retrieval-augmented answer engine for a developer-docs product. Answer using ONLY the retrieved context above — never use outside knowledge or guess. If the context does not contain the answer, say you don't know and point the user to where they might look. Cite every claim with its source as [filename#section]. Keep answers to 2-4 sentences unless asked for detail. Return strict JSON: {"answer": "...", "sources": ["..."], "confidence": 0.0-1.0}. Emit nothing outside the JSON. Cite every claim with its source as [filename#section]. It is important that you keep answers concise.`,
  "rag.rerank": `Rank the {{k}} candidate passages by how well they answer: "{{query}}".
Return a JSON array of passage ids, most relevant first. Drop anything off-topic.`,
  "rag.query_expansion": `Rewrite the user query into {{n}} diverse search queries covering synonyms and sub-questions.
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
  "summary.tldr": `You are a meticulous summarizer. Read the source material below and stay strictly faithful to it — do not add facts, opinions, or outside knowledge.
Produce {{n}} crisp bullet points for a busy executive; lead with the decision or the risk.

{{document}}`,
  "summary.section": `You are a meticulous summarizer. Read the source material below and stay strictly faithful to it — do not add facts, opinions, or outside knowledge.
Produce a {{length}}-word summary of the "{{section}}" section for a {{audience}} reader.

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

const RATE: Record<string, [number, number]> = {
  "gpt-4o-mini": [0.15, 0.6], "gpt-4o": [2.5, 10], "claude-haiku-4-5": [0.25, 1.25],
};

// per-prompt production profile: daily volume, the model it runs on, and avg
// token shape. Cost rollups + the trace sample are both derived from this so
// the numbers are internally consistent.
interface Prof { perDay: number; cost: number; tin: number; tout: number; model: string }
const PROFILE: Record<string, Prof> = {
  "rag.answer":         { perDay: 5200,  cost: 0.0040,  tin: 6800, tout: 300, model: "gpt-4o-mini" },
  "rag.rerank":         { perDay: 5200,  cost: 0.0016,  tin: 4200, tout: 140, model: "gpt-4o-mini" },
  "rag.query_expansion":{ perDay: 3400,  cost: 0.0006,  tin: 950,  tout: 110, model: "gpt-4o-mini" },
  "support.classify":   { perDay: 9800,  cost: 0.0005,  tin: 720,  tout: 38,  model: "gpt-4o-mini" },
  "support.reply":      { perDay: 1500,  cost: 0.0048,  tin: 2600, tout: 420, model: "claude-haiku-4-5" },
  "summary.tldr":       { perDay: 700,   cost: 0.0082,  tin: 6400, tout: 360, model: "gpt-4o" },
  "summary.section":    { perDay: 950,   cost: 0.0034,  tin: 2800, tout: 240, model: "claude-haiku-4-5" },
  "agent.planner":      { perDay: 620,   cost: 0.0220,  tin: 5200, tout: 300, model: "gpt-4o" },
  "agent.tool_select":  { perDay: 620,   cost: 0.0004,  tin: 720,  tout: 14,  model: "gpt-4o-mini" },
  "safety.guardrail":   { perDay: 11200, cost: 0.00028, tin: 520,  tout: 20,  model: "gpt-4o-mini" },
  "intent.router":      { perDay: 11200, cost: 0.00020, tin: 380,  tout: 9,   model: "gpt-4o-mini" },
  "chat.system":        { perDay: 2600,  cost: 0.0013,  tin: 1500, tout: 150, model: "gpt-4o-mini" },
};

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

// ---- suites: 5, varied states, real 16-run histories ----
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
    const score = Math.max(0.6, Math.min(0.99, s.latest_score + drift + noise));
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

// ---- trace sample: a recent ~2,500-row materialization for the traces list,
//      invocation detail, and per-prompt stats (the full month is ~1.6M calls).
const PROMPT_CDF = (() => {
  const entries = Object.entries(PROFILE);
  const total = entries.reduce((a, [, p]) => a + p.perDay, 0);
  let acc = 0;
  return entries.map(([n, p]) => { acc += p.perDay / total; return [acc, n] as [number, string]; });
})();
const pickPrompt = (r: number) => (PROMPT_CDF.find(([c]) => r <= c) || PROMPT_CDF[PROMPT_CDF.length - 1])[1];
const previewFor = (name: string) => {
  const m = moduleOf(name);
  if (m === "summary") return "• Renewal at risk: usage down 18% QoQ. • Two P1 bugs open >30d. • Expansion blocked on SSO.";
  if (name === "support.reply") return "Hi Dana — thanks for reaching out. Your plan does include priority support, so I've…";
  if (m === "agent") return '{"tool": "search_docs", "args": {"q": "rate limit free tier"}}';
  if (name === "safety.guardrail") return '{"allowed": false, "reason": "asks to disable content filtering"}';
  if (name === "intent.router") return "billing";
  return '{"answer": "100 requests per minute on the free plan.", "sources": ["billing.md#limits"], "confidence": 0.9}';
};
const INVOCATIONS = Array.from({ length: 2500 }, (_, i) => {
  const name = pickPrompt(rnd(i + 1));
  const p = PROFILE[name];
  const tin = Math.max(80, Math.round(p.tin * (0.6 + rnd(i + 7) * 0.9)));
  const tout = Math.max(8, Math.round(p.tout * (0.6 + rnd(i + 13) * 0.9)));
  const rate = RATE[p.model];
  const cost = (tin * rate[0] + tout * rate[1]) / 1e6;
  const rated = i % 3 === 0;
  const low = rated && rnd(i + 5) < 0.22;
  return {
    id: 90000 - i, prompt_name: name, prompt_version: Math.max(1, ver(name) - (i % 2)),
    created_at: iso(Math.floor(rnd(i + 3) * 30), Math.floor(rnd(i + 17) * 24)),
    model: p.model, tokens_in: tin, tokens_out: tout, cost: r2(cost, 6), latency_ms: 280 + Math.floor(rnd(i + 19) * 4200),
    has_capture: i % 3 === 0, output_preview: previewFor(name),
    rating: rated ? (low ? 0.4 : 0.95) : null,
    comment: low ? ["missed the figure", "wrong policy cited", "hallucinated a source"][i % 3] : null,
  };
});

// end-user feedback derived from the rated invocations, with varied ratings + notes
const POS_NOTES = ["spot on, thanks!", "exactly what I needed", "fast and accurate", "resolved my issue", "great answer"];
const NEG_NOTES = ["missed the figure", "wrong policy cited", "hallucinated a source", "didn't actually answer", "outdated info"];
const FB_SOURCES = ["web-widget", "thumbs", "in-app", "api"];
const FEEDBACK = INVOCATIONS.filter((r) => r.rating != null).map((r, i) => {
  const positive = r.rating! >= 0.7;
  const rating = positive ? [1, 0.9, 0.8, 0.95][i % 4] : [0.4, 0.2, 0.3][i % 3];
  const hasNote = !positive || i % 5 === 0;
  return {
    id: 70000 - i, request_id: "req-" + r.id, prompt_name: r.prompt_name, rating,
    comment: hasNote ? (positive ? POS_NOTES[i % POS_NOTES.length] : NEG_NOTES[i % NEG_NOTES.length]) : null,
    source: FB_SOURCES[i % FB_SOURCES.length], created_at: r.created_at, invocation_id: r.id, model: r.model,
  };
}).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

function feedbackList(qp: URLSearchParams) {
  const days = +(qp.get("days") || 30), cutoff = now - days * 864e5;
  let rows = FEEDBACK.filter((f) => new Date(f.created_at).getTime() >= cutoff);
  const name = qp.get("name"); if (name) rows = rows.filter((f) => f.prompt_name === name);
  if (qp.get("only_comments") === "true") rows = rows.filter((f) => f.comment && f.comment.trim());
  const minR = qp.get("min_rating"); if (minR != null) rows = rows.filter((f) => f.rating != null && f.rating <= +minR);
  const q = (qp.get("q") || "").toLowerCase();
  if (q) rows = rows.filter((f) => (f.comment || "").toLowerCase().includes(q) || f.prompt_name.toLowerCase().includes(q));
  const offset = +(qp.get("offset") || 0), limit = +(qp.get("limit") || 50);
  return { feedback: rows.slice(offset, offset + limit) };
}

function feedbackStats(days: number) {
  const cutoff = now - days * 864e5;
  const rows = FEEDBACK.filter((f) => new Date(f.created_at).getTime() >= cutoff);
  const rated = rows.map((f) => f.rating).filter((v): v is number => v != null);
  const positive = rated.filter((v) => v >= 0.7).length;
  const negative = rated.filter((v) => v <= 0.4).length;
  const bp: Record<string, { name: string; count: number; sum: number; neg: number }> = {};
  const byDay: Record<string, number[]> = {};
  for (const f of rows) {
    if (f.rating == null) continue;
    const e = (bp[f.prompt_name] ||= { name: f.prompt_name, count: 0, sum: 0, neg: 0 });
    e.count++; e.sum += f.rating; if (f.rating <= 0.4) e.neg++;
    (byDay[f.created_at.slice(0, 10)] ||= []).push(f.rating);
  }
  const spark = Object.keys(byDay).sort().map((d) => r2(byDay[d].filter((v) => v >= 0.7).length / byDay[d].length, 3)).slice(-14);
  return {
    days, total: rows.length, rated: rated.length, positive, negative, neutral: rated.length - positive - negative,
    with_comments: rows.filter((f) => f.comment && f.comment.trim()).length,
    positive_rate: rated.length ? r2(positive / rated.length, 4) : null,
    avg_rating: rated.length ? r2(rated.reduce((a, b) => a + b, 0) / rated.length, 4) : null,
    by_prompt: Object.values(bp).map((e) => ({ name: e.name, count: e.count, avg_rating: r2(e.sum / e.count, 3), negative: e.neg }))
      .sort((a, b) => b.negative - a.negative || b.count - a.count),
    sparkline: spark,
  };
}

// curated, production-scale cost rollups derived from PROFILE (not the sample)
function costResp(days: number, name?: string | null, model?: string | null) {
  const sel = Object.entries(PROFILE).filter(([n, p]) =>
    (!name || n === name || moduleOf(n) === name) && (!model || p.model === model));
  const by_name = sel.map(([n, p]) => {
    const calls = Math.round(p.perDay * days);
    return { name: n, calls, tokens_in: calls * p.tin, tokens_out: calls * p.tout, cost: r2(calls * p.cost, 2), models: [p.model] };
  }).sort((a, b) => b.cost - a.cost);
  const total_cost = by_name.reduce((a, r) => a + r.cost, 0);
  const total_calls = by_name.reduce((a, r) => a + r.calls, 0);
  const total_tin = by_name.reduce((a, r) => a + r.tokens_in, 0);
  const total_tout = by_name.reduce((a, r) => a + r.tokens_out, 0);
  // distribute over the window with a weekend dip + mild noise, normalized to the total
  const weights = Array.from({ length: days }, (_, d) => {
    const dow = new Date(now - (days - 1 - d) * 864e5).getDay();
    return (dow === 0 || dow === 6 ? 0.55 : 1) * (0.9 + rnd(d + 31) * 0.2);
  });
  const wsum = weights.reduce((a, b) => a + b, 0);
  const by_date = weights.map((w, d) => ({
    date: new Date(now - (days - 1 - d) * 864e5).toISOString().slice(0, 10),
    calls: Math.round((total_calls * w) / wsum),
    tokens_in: Math.round((total_tin * w) / wsum),
    tokens_out: Math.round((total_tout * w) / wsum),
    cost: r2((total_cost * w) / wsum, 2),
  }));
  return {
    summary: {
      total_cost: r2(total_cost, 2), total_calls, total_tokens_in: total_tin, total_tokens_out: total_tout,
      cache_hit_rate: 0.34, cache_savings: r2(total_cost * 0.27, 2), avg_cost: total_calls ? total_cost / total_calls : 0,
    },
    by_name, by_date,
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
  key_env: { openai: "OPENAI_API_KEY", anthropic: "ANTHROPIC_API_KEY", xai: "XAI_API_KEY", google: "GEMINI_API_KEY", azure: "AZURE_OPENAI_API_KEY" },
  cms_enabled: false,
  path: "~/helpdesk-ai/.promptry/config.toml",
};

// captured request/response, with a couple traces carrying PII/secrets to demo
// the scanner. Mask all-but-last-4, mirroring the backend's redact_text — the
// dashboard never re-displays what the scanner flagged.
const PII_EMAIL = "jordan.lee@northwind.io";
const PII_KEY = "sk-live-9f2ab7c41de83b6072aa";
const maskPII = (s: string) => "•".repeat(Math.min(8, Math.max(4, s.length - 4))) + s.slice(-4);
function captured(name: string, id: number) {
  const sys = (CONTENT[name] || "").split("\n").slice(0, 3).join("\n");
  let user = "How do I increase the rate limit on the free plan?";
  let out = previewFor(name);
  if (id % 9 === 0) user = `My account is ${maskPII(PII_EMAIL)} — why was I double charged?`;
  if (id % 23 === 0) out = `Use the admin key ${maskPII(PII_KEY)} to rotate it via the API.`;
  return { input_text: `System:\n${sys}\n\nUser: ${user}`, output_text: out };
}
function invocationDetail(id: number) {
  const r = INVOCATIONS.find((x) => x.id === id) || INVOCATIONS[0];
  const cap = captured(r.prompt_name, id);
  const tmpl = Math.round(r.tokens_in * 0.55);
  const rate = RATE[r.model];
  return {
    id: r.id, prompt_name: r.prompt_name, prompt_version: r.prompt_version, created_at: r.created_at,
    request_id: "req-" + r.id, metadata: { model: r.model, tokens_in: r.tokens_in, tokens_out: r.tokens_out, cost: r.cost, latency_ms: r.latency_ms },
    input_text: cap.input_text, output_text: cap.output_text,
    feedback: r.rating != null ? [{ rating: r.rating, comment: r.comment, source: "web-widget", created_at: r.created_at }] : [],
    breakdown: { available: true, model: r.model, template_tokens: tmpl, data_tokens: r.tokens_in - tmpl, tokens_out: r.tokens_out,
      template_cost: r2((tmpl * rate[0]) / 1e6, 6), data_cost: r2(((r.tokens_in - tmpl) * rate[0]) / 1e6, 6),
      output_cost: r2((r.tokens_out * rate[1]) / 1e6, 6), total_cost: r.cost, estimated: true },
  };
}
function scanResp(id: number) {
  const out: any = { input: [], output: [], total: 0, has_secret: false, worst_severity: null };
  if (id % 9 === 0) out.input.push({ type: "email", category: "pii", severity: "medium", count: 1, sample: maskPII(PII_EMAIL) });
  if (id % 23 === 0) { out.output.push({ type: "openai_api_key", category: "secret", severity: "high", count: 1, sample: maskPII(PII_KEY) }); out.has_secret = true; }
  out.total = out.input.length + out.output.length;
  out.worst_severity = out.has_secret ? "high" : out.total ? "medium" : null;
  return out;
}

const GOLDEN: Record<string, any[]> = {
  "rag.answer": [
    { id: 1, prompt_name: "rag.answer", input_text: "What is the rate limit on the free plan?", reference_output: '{"answer":"100 requests per minute","sources":["billing.md#limits"]}', source_invocation_id: 89990, model: "gpt-4o-mini", created_at: iso(2) },
    { id: 2, prompt_name: "rag.answer", input_text: "Do you offer SSO on the Team plan?", reference_output: '{"answer":"SSO is available on Team and Enterprise","sources":["security.md#sso"]}', source_invocation_id: 89975, model: "gpt-4o-mini", created_at: iso(5) },
    { id: 3, prompt_name: "rag.answer", input_text: "Can I export my data?", reference_output: '{"answer":"Yes, via Settings → Export (CSV/JSON)","sources":["data.md#export"]}', source_invocation_id: 89961, model: "gpt-4o", created_at: iso(8) },
  ],
  "support.classify": [
    { id: 4, prompt_name: "support.classify", input_text: "I was charged twice this month", reference_output: '{"intent":"billing","confidence":0.97}', source_invocation_id: 89950, model: "gpt-4o-mini", created_at: iso(3) },
    { id: 5, prompt_name: "support.classify", input_text: "The export button does nothing", reference_output: '{"intent":"bug","confidence":0.92}', source_invocation_id: 89944, model: "gpt-4o-mini", created_at: iso(6) },
  ],
};

// ---- prefix-cache analysis: a faithful JS port of promptry.prompt_diff
//      .prefix_cache_analysis so the demo's cache surfaces compute honestly
//      from CONTENT (same variable syntaxes, constants, and recommendations). ----
const _CHARS_PER_TOKEN = 4;
const _MIN_CACHE_TOKENS = 1024;
const _MEANINGFUL_REORDER_TOKENS = 25;
// {{name}} | ${name} | $name — mirrors promptry.prompt_diff._VAR.
const _VAR = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g;

// Python's round() is round-half-to-even; replicate it so token estimates match.
function roundHalfEven(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}
function estTokens(text: string): number {
  return Math.max(0, roundHalfEven((text || "").length / _CHARS_PER_TOKEN));
}

interface CacheSeg { type: "static" | "var"; text: string; name?: string }
function templateSegments(content: string): CacheSeg[] {
  content = content || "";
  const segs: CacheSeg[] = [];
  let pos = 0;
  _VAR.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = _VAR.exec(content)) !== null) {
    if (m.index > pos) segs.push({ type: "static", text: content.slice(pos, m.index) });
    const name = m[1] || m[2] || m[3];
    segs.push({ type: "var", text: m[0], name });
    pos = m.index + m[0].length;
  }
  if (pos < content.length) segs.push({ type: "static", text: content.slice(pos) });
  return segs;
}

function prefixCacheAnalysis(content: string, avgInputTokens?: number | null,
                            minCacheTokens: number = _MIN_CACHE_TOKENS) {
  content = content || "";
  const segs = templateSegments(content);

  const variables: string[] = [];
  for (const s of segs) if (s.type === "var" && s.name && !variables.includes(s.name)) variables.push(s.name);

  // Offset of the first variable = end of the guaranteed-cacheable prefix.
  let firstVar: string | null = null;
  let firstVarOffset: number | null = null;
  let off = 0;
  for (const s of segs) {
    if (s.type === "var") { firstVar = s.name ?? null; firstVarOffset = off; break; }
    off += s.text.length;
  }

  const totalChars = content.length;
  const prefixChars = firstVarOffset !== null ? firstVarOffset : totalChars;
  const staticChars = segs.filter((s) => s.type === "static").reduce((a, s) => a + s.text.length, 0);

  const cacheableNow = estTokens(content.slice(0, prefixChars));
  const cacheablePotential = estTokens(segs.filter((s) => s.type === "static").map((s) => s.text).join(""));
  const reorderGain = Math.max(0, cacheablePotential - cacheableNow);
  const thresholdTokens = avgInputTokens != null ? avgInputTokens : estTokens(content);
  const meetsThreshold = thresholdTokens >= minCacheTokens;

  let rec: string;
  let rationale: string;
  if (variables.length === 0) {
    rec = "no_variables";
    rationale = "This template has no input variables — it's fully static, so the " +
      "whole prompt is a cacheable prefix (a template with no inputs is unusual, though).";
  } else if (!meetsThreshold) {
    rec = "too_small";
    const src = avgInputTokens != null ? "its measured requests are" : "it is";
    rationale = `At ~${thresholdTokens} tokens ${src} below the ~${minCacheTokens}-token ` +
      "floor where OpenAI/Anthropic prefix caching activates, so caching won't " +
      "apply regardless of ordering. (If real inputs are much larger than this " +
      "estimate, re-check with telemetry.)";
  } else if (reorderGain < _MEANINGFUL_REORDER_TOKENS) {
    rec = "already_optimal";
    rationale = "Inputs already sit at the end — the static instruction block " +
      `(~${cacheableNow} tokens) forms one cacheable prefix. Nothing to do.`;
  } else {
    rec = "move_inputs_to_end";
    rationale = `Your first input {{${firstVar}}} appears after only ~${cacheableNow} ` +
      `cacheable tokens, but ~${cacheablePotential} tokens of this template are ` +
      "static. Moving all inputs to the end would extend the cacheable prefix by " +
      `~${reorderGain} tokens, so every repeat call re-bills that much less.`;
  }

  return {
    total_chars: totalChars,
    total_tokens: estTokens(content),
    variables,
    first_variable: firstVar,
    cacheable_prefix_chars: prefixChars,
    cacheable_prefix_tokens: cacheableNow,
    static_total_chars: staticChars,
    static_total_tokens: cacheablePotential,
    reorder_gain_tokens: reorderGain,
    min_cache_tokens: minCacheTokens,
    threshold_tokens: thresholdTokens,
    meets_threshold: meetsThreshold,
    segments: segs,
    recommendation: rec as
      "move_inputs_to_end" | "already_optimal" | "too_small" | "no_variables",
    rationale,
  };
}

const _CACHE_REC_RANK: Record<string, number> = {
  move_inputs_to_end: 0, no_variables: 1, already_optimal: 2, too_small: 3,
};

// ---- shorten analysis: a rule-based port of promptry.prompt_diff
//      .shorten_analysis. The demo runs the deterministic tier (duplicate,
//      filler, redundant_format, whitespace) honestly from CONTENT; the
//      embedding-backed semantic tier is stood in with ONE hardcoded finding on
//      a prompt with a semantically-redundant line, so the demo shows the tier. ----
const _FILLER_RE = /please |kindly |I want you to |I need you to |I would like you to |Your task is to |You are asked to |It is important that |It is important to |It is crucial that |Please note that |Note that |as much as possible|to the best of your ability|in order to |make sure to |be sure to /gi;
const _FORMAT_KEYWORDS = ["json", "yaml", "markdown", "xml"];

function normSentence(s: string): string {
  return s.replace(/\s+/g, " ").trim().toLowerCase().replace(/^[.!?,:;"'\s]+|[.!?,:;"'\s]+$/g, "");
}
function jaccard(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const t of a) if (b.has(t)) inter++;
  const union = a.size + b.size - inter;
  return union ? inter / union : 0;
}

interface ShortenFinding { kind: string; message: string; text: string; counterpart?: string; tokens: number; similarity?: number }

// One hardcoded semantic_duplicate per prompt (text must occur in the static
// text). rag.answer restates "answer using only the retrieved context" in two
// different wordings — the embedding tier would catch it; here we assert it.
const _SEMANTIC_DUP: Record<string, { text: string; counterpart: string; similarity: number }> = {
  "rag.answer": { text: "Answer using ONLY the retrieved context above", counterpart: "never use outside knowledge or guess", similarity: 0.9 },
};

function shortenAnalysis(content: string, name?: string) {
  content = content || "";
  const segs = templateSegments(content);
  const staticText = segs.filter((s) => s.type === "static").map((s) => s.text).join("");
  const totalTokens = estTokens(staticText);
  const findings: ShortenFinding[] = [];

  // A. duplicate + near-duplicate sentences
  const sentences: { raw: string; norm: string; toks: Set<string> }[] = [];
  for (const raw of staticText.split(/[.!?\n]+/)) {
    const norm = normSentence(raw);
    if (!norm) continue;
    sentences.push({ raw: raw.trim(), norm, toks: new Set(norm.split(" ")) });
  }
  const seen = new Map<string, number>();
  sentences.forEach((s, idx) => {
    if (seen.has(s.norm)) {
      findings.push({ kind: "duplicate", message: "Repeated instruction — already stated above.", text: s.raw, counterpart: sentences[seen.get(s.norm)!].raw, tokens: estTokens(s.raw) });
      return;
    }
    let nearIdx = -1;
    for (const pIdx of seen.values()) if (jaccard(s.toks, sentences[pIdx].toks) >= 0.8) { nearIdx = pIdx; break; }
    if (nearIdx >= 0) findings.push({ kind: "duplicate", message: "Near-duplicate of an earlier sentence — likely redundant.", text: s.raw, counterpart: sentences[nearIdx].raw, tokens: estTokens(s.raw) });
    seen.set(s.norm, idx);
  });

  // filler phrases
  _FILLER_RE.lastIndex = 0;
  let fm: RegExpExecArray | null;
  while ((fm = _FILLER_RE.exec(staticText)) !== null) {
    findings.push({ kind: "filler", message: "Filler phrase models ignore.", text: fm[0], tokens: estTokens(fm[0]) });
  }

  // over-repeated format keywords
  for (const kw of _FORMAT_KEYWORDS) {
    const count = (staticText.match(new RegExp("\\b" + kw + "\\b", "gi")) || []).length;
    if (count > 2) findings.push({ kind: "redundant_format", message: `'${kw.toUpperCase()}' mentioned ${count} times.`, text: kw, tokens: (count - 1) * estTokens(kw) });
  }

  // excess blank lines
  const wsRe = /\n{3,}/g;
  let wm: RegExpExecArray | null;
  while ((wm = wsRe.exec(staticText)) !== null) {
    findings.push({ kind: "whitespace", message: "Excessive blank lines — collapse to a single break.", text: wm[0], tokens: estTokens(wm[0].slice(2)) });
  }

  // B. semantic tier (mock stand-in: one hardcoded finding)
  const sd = name ? _SEMANTIC_DUP[name] : undefined;
  if (sd && staticText.includes(sd.text)) {
    findings.push({ kind: "semantic_duplicate", message: "Semantically redundant with an earlier sentence.", text: sd.text, counterpart: sd.counterpart, tokens: estTokens(sd.text), similarity: sd.similarity });
  }

  const byText = new Map<string, number>();
  for (const f of findings) byText.set(f.text, f.tokens);
  let saved = 0;
  for (const v of byText.values()) saved += v;
  saved = Math.min(totalTokens, saved);

  findings.sort((a, b) => b.tokens - a.tokens);
  return { total_tokens: totalTokens, est_tokens_saved: saved, semantic_available: true, findings };
}

// ---- route table ----
const routes: [RegExp, (m: RegExpMatchArray, q: URLSearchParams, body: any) => any][] = [
  [/^\/api\/health$/, async () => ({ status: "ok", version: await liveVersion(), db_path: "helpdesk-ai.db" })],
  [/^\/api\/onboarding-status$/, () => ({ suites: SUITES.length, prompts: promptSummaries.length, invocations: 1, empty: false })],
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
    { a: "summary.tldr", b: "summary.section", similarity: 0.9 },
  ] })],
  [/^\/api\/prompts\/diff2$/, (_m, q) => {
    const a = q.get("a") || "summary.tldr";
    const b = q.get("b") || "summary.section";
    const ca = CONTENT[a] || "";
    const cb = CONTENT[b] || "";

    // Real char-level diff: common prefix + common suffix + differing middle.
    let prefixLen = 0;
    const minLen = Math.min(ca.length, cb.length);
    while (prefixLen < minLen && ca[prefixLen] === cb[prefixLen]) prefixLen++;
    const remA = ca.slice(prefixLen);
    const remB = cb.slice(prefixLen);
    let suffixLen = 0;
    const minRem = Math.min(remA.length, remB.length);
    while (suffixLen < minRem && remA[remA.length - 1 - suffixLen] === remB[remB.length - 1 - suffixLen]) suffixLen++;
    const aMiddle = remA.slice(0, remA.length - suffixLen);
    const bMiddle = remB.slice(0, remB.length - suffixLen);
    const suffix = remA.slice(remA.length - suffixLen);

    const diff = [
      { type: "equal", text: ca.slice(0, prefixLen) },
      { type: "delete", text: aMiddle },
      { type: "insert", text: bMiddle },
      { type: "equal", text: suffix },
    ].filter((s) => s.text) as { type: "equal" | "insert" | "delete"; text: string }[];

    const ratio = r2(prefixLen / Math.max(ca.length, cb.length, 1), 4);
    const suggested = prefixLen >= 20 && ratio >= 0.15 && ratio < 1;
    return {
      a: { name: a, latest_version: ver(a), content: ca },
      b: { name: b, latest_version: ver(b), content: cb },
      diff,
      shared_prefix_chars: prefixLen,
      shared_prefix_ratio: ratio,
      cache_suggestion: {
        suggested,
        rationale: suggested
          ? `These prompts share ${prefixLen} identical leading characters (${Math.round(ratio * 100)}% of the longer prompt); caching that prefix would cut input cost on every call to both.`
          : `Only ${prefixLen} leading characters are shared — too little to make a shared prefix cache worthwhile.`,
      },
    };
  }],
  // MUST precede the generic /api/prompts/:name route below (cache-analysis
  // has no slash, so it would otherwise be captured as a prompt name).
  [/^\/api\/prompts\/cache-analysis$/, () => ({
    prompts: NAMES.map((name) => {
      const a = prefixCacheAnalysis(CONTENT[name], PROFILE[name]?.tin);
      return {
        name, version: ver(name), recommendation: a.recommendation,
        cacheable_prefix_tokens: a.cacheable_prefix_tokens,
        static_total_tokens: a.static_total_tokens,
        reorder_gain_tokens: a.reorder_gain_tokens,
        total_tokens: a.total_tokens,
        threshold_tokens: a.threshold_tokens,
        meets_threshold: a.meets_threshold,
        first_variable: a.first_variable,
      };
    }).sort((x, y) =>
      (_CACHE_REC_RANK[x.recommendation] - _CACHE_REC_RANK[y.recommendation]) ||
      (y.reorder_gain_tokens - x.reorder_gain_tokens)),
  })],
  // Per-prompt cache detail. Before /content$ and the generic /:name routes.
  [/^\/api\/prompts\/([^/]+)\/cache-analysis$/, (m) => {
    const name = decodeURIComponent(m[1]);
    const a = prefixCacheAnalysis(CONTENT[name], PROFILE[name]?.tin);
    return { name, version: ver(name), content: CONTENT[name] || "", ...a };
  }],
  // Shorten analysis: ranked list. MUST precede the generic /:name route (no
  // slash, so it would otherwise be captured as a prompt name).
  [/^\/api\/prompts\/shorten-analysis$/, () => ({
    prompts: NAMES.map((name) => {
      const a = shortenAnalysis(CONTENT[name], name);
      return { name, version: ver(name), est_tokens_saved: a.est_tokens_saved, finding_count: a.findings.length, total_tokens: a.total_tokens };
    }).sort((x, y) => y.est_tokens_saved - x.est_tokens_saved),
  })],
  // Per-prompt shorten detail. Before /content$ and the generic /:name routes.
  [/^\/api\/prompts\/([^/]+)\/shorten-analysis$/, (m) => {
    const name = decodeURIComponent(m[1]);
    const a = shortenAnalysis(CONTENT[name], name);
    return { name, version: ver(name), content: CONTENT[name] || "", ...a };
  }],
  [/^\/api\/suite-candidates$/, (_m, q) => {
    const source = q.get("source") || "golden";
    const cands = source === "feedback"
      ? [
          { question: "How do I export my data?", context: "", response: "", source: "feedback", request_id: "req_8812", prompt_name: "rag.answer" },
          { question: "Does the free plan include SSO?", context: "billing.md#sso: SSO is on Team and Enterprise.", response: "SSO is available on Team and Enterprise plans.", source: "feedback", request_id: "req_8790", prompt_name: "rag.answer" },
        ]
      : [
          { question: "What is the rate limit on the free plan?", context: "billing.md#limits: 100 rpm, 10k/day.", response: "100 requests per minute (10k/day).", source: "golden", request_id: "req_9001", prompt_name: "rag.answer" },
          { question: "Can I self-host?", context: "docs/deploy.md: Docker and Helm supported.", response: "Yes — via Docker or Helm.", source: "golden", request_id: "req_9002", prompt_name: "rag.answer" },
        ];
    const someMissing = cands.some((c) => !c.context || !c.response);
    return { candidates: cands, capture_note: someMissing ? "Some candidates have no captured context/response — enable capture to include full RAG cases." : null };
  }],
  [/^\/api\/suites\/([^/]+)\/definition$/, (m) => {
    const name = decodeURIComponent(m[1]);
    return {
      editable: true, source: "yaml", path: "evals.yaml",
      definition: {
        name, model: "gpt-4o-mini", prompt: "You are the support assistant. Context:\n{{context}}\n\nQ: {{question}}",
        cases: [
          { input: "What is the rate limit on the free plan?", context: "billing.md#limits: 100 rpm, 10k/day.", expect: [{ type: "contains", value: "100" }] },
          { input: "Can I self-host?", context: "docs/deploy.md: Docker and Helm supported.", expect: [{ type: "semantic", value: "Yes, via Docker or Helm." }] },
        ],
      },
    };
  }],
  [/^\/api\/prompts\/([^/]+)\/recorded-context$/, (m) => {
    const name = decodeURIComponent(m[1]);
    return { name, context: "billing.md#limits: 100 rpm, 10k/day.\n---\ndocs/plans.md: Free, Team, Enterprise.", found: true };
  }],
  [/^\/api\/prompts\/([^/]+)\/content$/, (m, q) => contentResp(decodeURIComponent(m[1]), q.get("v") ? +q.get("v")! : undefined)],
  [/^\/api\/prompts\/([^/]+)\/diff$/, (m) => {
    const first = (CONTENT[decodeURIComponent(m[1])] || "You are an assistant.").split("\n")[0];
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
    return { name, days: 30, total_calls: Math.round((PROFILE[name]?.perDay || 1000) * 30), drifting_count: drifting ? 2 : 0, status: drifting ? "drifting" : "stable", metrics: [
      { metric: "cost", label: "cost / call", count: 40, baseline_mean: 0.0031, recent_mean: drifting ? 0.0048 : 0.0032, pct_change: drifting ? 0.54 : 0.03, slope: 0.00001, p_value: drifting ? 0.018 : 0.4, direction: "up", bad_direction: "up", drifting, severity: drifting ? "high" : "none", message: drifting ? "cost / call ↑ +54% (p=0.018)" : "cost / call → +3% (p=0.400)" },
      { metric: "rating", label: "feedback rating", count: 22, baseline_mean: 0.91, recent_mean: drifting ? 0.79 : 0.9, pct_change: drifting ? -0.13 : -0.01, slope: 0, p_value: drifting ? 0.03 : 0.7, direction: "down", bad_direction: "down", drifting, severity: drifting ? "medium" : "none", message: drifting ? "feedback rating ↓ -13% (p=0.030)" : "feedback rating → -1% (p=0.700)" },
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
  [/^\/api\/cost\/coverage$/, (_m, q) => ({ days: +(q.get("days") || 14), models_seen: 3, uncosted: [], uncosted_calls: 0 })],
  [/^\/api\/cost$/, (_m, q) => costResp(+(q.get("days") || 14), q.get("name"), q.get("model"))],
  [/^\/api\/feedback\/stats$/, (_m, q) => feedbackStats(+(q.get("days") || 30))],
  [/^\/api\/feedback$/, (_m, q) => feedbackList(q)],
  [/^\/api\/config$/, () => CONFIG],
  [/^\/api\/budgets$/, () => ({ budgets: [
    { id: 1, scope: "global", target: null, period: "monthly", limit_usd: 3000, spend: 2247.6, pct: 74.9, breached: false },
    { id: 2, scope: "module", target: "rag", period: "daily", limit_usd: 40, spend: 36.3, pct: 90.8, breached: false },
    { id: 3, scope: "prompt", target: "agent.planner", period: "monthly", limit_usd: 350, spend: 409.2, pct: 116.9, breached: true },
  ] })],
  [/^\/api\/invocations\/(\d+)\/scan$/, (m) => scanResp(+m[1])],
  [/^\/api\/invocations\/(\d+)$/, (m) => invocationDetail(+m[1])],
  [/^\/api\/invocations$/, (_m, q) => {
    let rows = [...INVOCATIONS];
    const name = q.get("name"); if (name) rows = rows.filter((r) => r.prompt_name === name);
    if (q.get("captured_only") === "true") rows = rows.filter((r) => r.has_capture);
    const minR = q.get("min_rating"); if (minR != null) rows = rows.filter((r) => r.rating != null && r.rating <= +minR);
    const sort = q.get("sort");
    if (sort) {
      const dir = q.get("direction") === "asc" ? 1 : -1;
      const key = (r: any) => sort === "created_at" ? new Date(r.created_at).getTime() : (r[sort] ?? -Infinity);
      rows.sort((a, b) => (key(a) - key(b)) * dir);
    } else {
      rows.sort((a, b) => q.get("order") === "cost" ? (b.cost || 0) - (a.cost || 0) : new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
    const offset = +(q.get("offset") || 0), limit = +(q.get("limit") || 100);
    return { invocations: rows.slice(offset, offset + limit) };
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
  if (path.endsWith("/api/suites")) {
    return { ok: true, name: body?.name || "new.suite", cases: Array.isArray(body?.cases) ? body.cases.length : 0 };
  }
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
      const data = await match(path, method, body);
      await new Promise((res) => setTimeout(res, 90));
      return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return orig(input, init);
  };
}
