/* ============================================================
   promptry demo dashboard — synthetic data + views.
   No backend, no build step. Mirrors the real dashboard pages:
   Overview, Prompts (+ detail w/ version diff), Cost (module →
   prompt → call drill-down), Invocation detail, Playground.
   Every number here is fabricated for demonstration.
   ============================================================ */
(function () {
  "use strict";

  // ---------------- formatting helpers ----------------
  const usd = (n, d) => (n == null ? "—" : "$" + Number(n).toFixed(d == null ? (n < 0.01 ? 6 : 4) : d));
  const fmtTok = (n) => {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return String(Math.round(n));
  };
  const pct = (x, d) => (x == null ? "—" : (x * 100).toFixed(d == null ? 0 : d) + "%");
  const num = (n) => Number(n).toLocaleString();
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const scoreColor = (s) => (s == null ? "var(--muted)" : s >= 0.8 ? "var(--success)" : s >= 0.6 ? "var(--warning)" : "var(--error)");

  // ---------------- synthetic data ----------------
  const SUITES = [
    { name: "rag-regression", latest_score: 0.72, passed: false, drift: "drifting", model: "gpt-4o", pver: 4, when: "12m ago", spark: [0.91, 0.90, 0.92, 0.89, 0.88, 0.86, 0.84, 0.81, 0.78, 0.72] },
    { name: "pricing-extract", latest_score: 0.94, passed: true, drift: "stable", model: "gpt-4o", pver: 7, when: "1h ago", spark: [0.90, 0.91, 0.93, 0.92, 0.94, 0.93, 0.95, 0.94, 0.93, 0.94] },
    { name: "doc-classify", latest_score: 0.97, passed: true, drift: "stable", model: "gpt-4o-mini", pver: 2, when: "3h ago", spark: [0.95, 0.96, 0.96, 0.97, 0.96, 0.97, 0.98, 0.97, 0.97, 0.97] },
    { name: "agent-tools", latest_score: 0.88, passed: true, drift: "stable", model: "claude-sonnet-4", pver: 3, when: "5h ago", spark: [0.84, 0.85, 0.87, 0.86, 0.88, 0.87, 0.89, 0.88, 0.88, 0.88] },
    { name: "safety-audit", latest_score: 0.64, passed: false, drift: "stable", model: "gpt-4o", pver: 1, when: "1d ago", spark: [0.70, 0.68, 0.66, 0.67, 0.65, 0.66, 0.64, 0.65, 0.63, 0.64] },
    { name: "chatbot-flow", latest_score: 0.91, passed: true, drift: "drifting", model: "claude-haiku-4-5", pver: 5, when: "2d ago", spark: [0.95, 0.94, 0.94, 0.93, 0.92, 0.93, 0.91, 0.92, 0.90, 0.91] },
  ];

  // Cost rows (one per prompt) — names are module.prompt
  const COST_BY_NAME = [
    { name: "pricing.extract", calls: 8470, tokens_in: 4_235_000, tokens_out: 847_000, cost: 25.41, cache_hit: 0.41, models: ["gpt-4o"] },
    { name: "pricing.line_items", calls: 3120, tokens_in: 1_872_000, tokens_out: 312_000, cost: 11.86, cache_hit: 0.38, models: ["gpt-4o"] },
    { name: "rag.qa", calls: 24210, tokens_in: 12_100_000, tokens_out: 1_452_000, cost: 9.74, cache_hit: 0.62, models: ["gpt-4o-mini", "gpt-4o"] },
    { name: "rag.rerank", calls: 24210, tokens_in: 2_421_000, tokens_out: 121_000, cost: 1.21, cache_hit: 0.71, models: ["gpt-4o-mini"] },
    { name: "agent.plan", calls: 1840, tokens_in: 3_312_000, tokens_out: 552_000, cost: 8.62, cache_hit: 0.33, models: ["claude-sonnet-4"] },
    { name: "agent.tool_select", calls: 5520, tokens_in: 1_656_000, tokens_out: 110_400, cost: 1.93, cache_hit: 0.48, models: ["claude-haiku-4-5"] },
    { name: "summary.daily", calls: 720, tokens_in: 1_440_000, tokens_out: 216_000, cost: 4.32, cache_hit: 0.55, models: ["gpt-4o"] },
    { name: "classify.intent", calls: 9300, tokens_in: 930_000, tokens_out: 46_500, cost: 0.42, cache_hit: 0.0, models: ["llama-3.3-70b"] },
  ];

  const COST_BY_DATE = [
    { date: "05-12", cost: 1.84 }, { date: "05-13", cost: 2.10 }, { date: "05-14", cost: 1.96 },
    { date: "05-15", cost: 3.40 }, { date: "05-16", cost: 4.62 }, { date: "05-17", cost: 2.88 },
    { date: "05-18", cost: 3.11 }, { date: "05-19", cost: 5.02 }, { date: "05-20", cost: 4.41 },
    { date: "05-21", cost: 6.20 }, { date: "05-22", cost: 5.74 }, { date: "05-23", cost: 4.10 },
    { date: "05-24", cost: 7.18 }, { date: "05-25", cost: 6.85 },
  ];

  const COVERAGE = { models_seen: 5, uncosted: [{ model: "llama-3.3-70b", calls: 9300 }], uncosted_calls: 9300 };

  const BUDGETS = [
    { id: 1, scope: "global", target: null, period: "monthly", limit_usd: 100, spend: 63.51, pct: 63.5, breached: false },
    { id: 2, scope: "module", target: "pricing", period: "monthly", limit_usd: 30, spend: 37.27, pct: 124.2, breached: true },
    { id: 3, scope: "module", target: "rag", period: "daily", limit_usd: 2, spend: 1.42, pct: 71.0, breached: false },
  ];

  // Prompts registry — grouped by module
  const PROMPTS = [
    { name: "pricing.extract", v: 7, tags: ["prod"] },
    { name: "pricing.line_items", v: 4, tags: ["prod", "staging"] },
    { name: "rag.qa", v: 12, tags: ["prod"] },
    { name: "rag.rerank", v: 3, tags: [] },
    { name: "agent.plan", v: 6, tags: ["staging"] },
    { name: "agent.tool_select", v: 2, tags: [] },
    { name: "summary.daily", v: 9, tags: ["prod"] },
    { name: "classify.intent", v: 2, tags: [] },
    { name: "safety.refusal", v: 1, tags: ["critical"] },
  ];

  // Detailed version history + content for rag.qa (the demo's deep-dive prompt)
  const RAGQA_VERSIONS = [
    { v: 12, hash: "a3f91c4", when: "12m ago", tags: ["prod"], source: "dashboard_edit",
      content: "You are a precise question-answering assistant.\nAnswer the user's question using ONLY the provided context.\nIf the answer is not supported by the context, reply exactly: \"I don't know.\"\nCite the source filename for every claim, like [billing.md].\n\nContext:\n$context\n\nQuestion: $question\n\nRespond in JSON: {\"answer\": ..., \"sources\": [...]}" },
    { v: 11, hash: "7b20de8", when: "4h ago", tags: [], source: "dashboard_edit",
      content: "You are a precise question-answering assistant.\nAnswer the user's question using ONLY the provided context.\nIf the answer is not supported by the context, reply exactly: \"I don't know.\"\n\nContext:\n$context\n\nQuestion: $question\n\nRespond in JSON: {\"answer\": ..., \"sources\": [...]}" },
    { v: 10, hash: "c91a0f2", when: "2d ago", tags: [], source: "seed_default",
      content: "You are a question-answering assistant.\nAnswer using the provided context.\n\nContext:\n$context\n\nQuestion: $question" },
  ];

  // Invocations for rag.qa (recent + cost-sorted reuse)
  const RAGQA_INVOCATIONS = [
    { id: 90412, model: "gpt-4o", tokens_in: 8040, tokens_out: 512, cost: 0.0252, latency: 1840, when: "2m ago", rating: 1, preview: '{"answer":"100 requests per minute on the free plan","sources":["billing.md"]}' },
    { id: 90408, model: "gpt-4o", tokens_in: 12400, tokens_out: 640, cost: 0.0374, latency: 2210, when: "6m ago", rating: -1, preview: '{"answer":"Refunds are available up to 60 days","sources":["refunds.md"]}' },
    { id: 90401, model: "gpt-4o-mini", tokens_in: 3200, tokens_out: 180, cost: 0.0006, latency: 720, when: "11m ago", rating: null, preview: '{"answer":"I don\'t know.","sources":[]}' },
    { id: 90390, model: "gpt-4o", tokens_in: 6800, tokens_out: 420, cost: 0.0212, latency: 1610, when: "18m ago", rating: 1, preview: '{"answer":"The pro plan allows 1000 req/min","sources":["billing.md"]}' },
    { id: 90377, model: "gpt-4o-mini", tokens_in: 2950, tokens_out: 150, cost: 0.0005, latency: 690, when: "25m ago", rating: null, preview: '{"answer":"Burst over the limit returns HTTP 429","sources":["billing.md"]}' },
  ];

  // The single invocation detail (90408 — the downvoted, pricey one)
  const INVOCATION = {
    id: 90408, prompt_name: "rag.qa", prompt_version: 12, when: "6m ago", request_id: "req_8f2a1c",
    model: "gpt-4o", tokens_in: 12400, tokens_out: 640, cost: 0.0374, latency: 2210,
    template_tokens: 96, data_tokens: 12304, tokens_out_n: 640,
    template_cost: 0.00024, data_cost: 0.03076, output_cost: 0.0064,
    feedback: [
      { rating: -1, comment: "Said 60-day refund window; the policy doc says 30 days. Fabricated.", source: "thumbs", when: "5m ago" },
    ],
    input_text: "Context:\n# refunds.md\n- refunds allowed within 30 days of invoice\n- prorated refunds are not offered\n- cancellations after 30d are effective at period end\n\nQuestion: I cancelled on day 45 — can I get a refund?",
    output_text: '{\n  "answer": "Refunds are available up to 60 days after the invoice date, so you may still be eligible.",\n  "sources": ["refunds.md"]\n}',
  };

  // ---------------- icons ----------------
  const ICONS = {
    overview: '<path d="M3 3h4v4H3zM9 3h4v4H9zM3 9h4v4H3zM9 9h4v4H9z" stroke="currentColor" stroke-width="1.3" fill="none"/>',
    evals: '<path d="M2 8.5L6 12l8-9" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    prompts: '<path d="M4 2h6l2 2v10H4zM4 6h8M4 9h8M4 12h5" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linejoin="round"/>',
    models: '<g stroke="currentColor" stroke-width="1.3" fill="none"><circle cx="5" cy="5" r="2.5"/><circle cx="11" cy="11" r="2.5"/><path d="M5 7.5v3M7.5 5h3"/></g>',
    cost: '<path d="M8 2v12M11 5a3 3 0 0 0-3-1.5c-2 0-2.5 1-2.5 2S6 7 8 7.5s2.5 1.5 2.5 2.5S9.5 12 8 12c-1.5 0-2.5-.5-3-1.5" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/>',
    playground: '<path d="M8 2l5 3v6l-5 3-5-3V5z M8 2v12M3 5l10 6M13 5L3 11" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linejoin="round"/>',
  };

  const NAV = [
    { id: "overview", label: "Overview" },
    { id: "prompts", label: "Prompts" },
    { id: "cost", label: "Cost" },
    { id: "invocation", label: "Invocation" },
    { id: "playground", label: "Playground" },
  ];

  // ---------------- sparkline / charts ----------------
  function sparkline(scores, w, h) {
    w = w || 92; h = h || 24;
    if (!scores || scores.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
    const pad = 2, iw = w - pad * 2, ih = h - pad * 2;
    const min = Math.min(...scores), max = Math.max(...scores), range = max - min || 1;
    const pts = scores.map((s, i) => [pad + (i / (scores.length - 1)) * iw, pad + (1 - (s - min) / range) * ih]);
    const poly = pts.map((p) => p.join(",")).join(" ");
    const area = `${pad},${pad + ih} ${poly} ${pad + iw},${pad + ih}`;
    const c = scoreColor(scores[scores.length - 1]);
    const gid = "sg" + Math.round(Math.random() * 1e6);
    const lp = pts[pts.length - 1];
    return `<svg width="${w}" height="${h}" style="overflow:visible"><defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="${c}" stop-opacity="0.25"/><stop offset="100%" stop-color="${c}" stop-opacity="0"/></linearGradient></defs><polygon points="${area}" fill="url(#${gid})"/><polyline points="${poly}" fill="none" stroke="${c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="${lp[0]}" cy="${lp[1]}" r="2" fill="${c}" stroke="var(--bg)" stroke-width="1"/></svg>`;
  }

  function barChart(data, valueKey, labelKey, height) {
    height = height || 200;
    const w = 760, pad = { l: 46, r: 14, t: 14, b: 28 };
    const iw = w - pad.l - pad.r, ih = height - pad.t - pad.b;
    const max = Math.max(...data.map((d) => d[valueKey])) * 1.15 || 1;
    const bw = iw / data.length;
    const ticks = [0, max / 2, max];
    let g = "";
    ticks.forEach((t) => {
      const y = pad.t + (1 - t / max) * ih;
      g += `<line x1="${pad.l}" x2="${pad.l + iw}" y1="${y}" y2="${y}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      g += `<text x="${pad.l - 8}" y="${y + 3}" text-anchor="end" fill="var(--muted)" font-size="10" font-family="var(--font-mono)">$${t.toFixed(2)}</text>`;
    });
    data.forEach((d, i) => {
      const v = d[valueKey], x = pad.l + i * bw + bw * 0.15, hh = (v / max) * ih, y = pad.t + ih - hh, hw = bw * 0.7;
      g += `<rect x="${x}" y="${y}" width="${hw}" height="${hh}" rx="3" fill="var(--accent)"><title>${d[labelKey]}: $${v.toFixed(2)}</title></rect>`;
      g += `<text x="${x + hw / 2}" y="${pad.t + ih + 15}" text-anchor="middle" font-size="9.5" fill="var(--muted)" font-family="var(--font-mono)">${d[labelKey]}</text>`;
    });
    return `<svg viewBox="0 0 ${w} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet" style="display:block">${g}</svg>`;
  }

  function scoreChart(scores, height) {
    height = height || 200;
    const w = 760, pad = { l: 38, r: 14, t: 14, b: 26 };
    const iw = w - pad.l - pad.r, ih = height - pad.t - pad.b;
    const min = 0.3, max = 1.0;
    const xs = (i) => pad.l + (i / (scores.length - 1)) * iw;
    const ys = (s) => pad.t + (1 - (s - min) / (max - min)) * ih;
    const poly = scores.map((s, i) => `${xs(i)},${ys(s)}`).join(" ");
    const area = `${pad.l},${pad.t + ih} ${poly} ${pad.l + iw},${pad.t + ih}`;
    let g = "";
    [0.4, 0.6, 0.8, 1.0].forEach((t) => {
      g += `<line x1="${pad.l}" x2="${pad.l + iw}" y1="${ys(t)}" y2="${ys(t)}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      g += `<text x="${pad.l - 8}" y="${ys(t) + 3}" text-anchor="end" fill="var(--muted)" font-size="10" font-family="var(--font-mono)">${Math.round(t * 100)}%</text>`;
    });
    g += `<line x1="${pad.l}" x2="${pad.l + iw}" y1="${ys(0.8)}" y2="${ys(0.8)}" stroke="var(--success)" stroke-opacity="0.25"/>`;
    g += `<text x="${pad.l + iw - 2}" y="${ys(0.8) - 4}" text-anchor="end" font-size="9" fill="var(--success)" opacity="0.7" font-family="var(--font-mono)">target 80%</text>`;
    g += `<defs><linearGradient id="ag" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity="0.24"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>`;
    g += `<polygon points="${area}" fill="url(#ag)"/>`;
    g += `<polyline points="${poly}" fill="none" stroke="var(--accent)" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/>`;
    scores.forEach((s, i) => { g += `<circle cx="${xs(i)}" cy="${ys(s)}" r="1.8" fill="var(--bg)" stroke="var(--accent)" stroke-width="1.25"/>`; });
    return `<svg viewBox="0 0 ${w} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet" style="display:block">${g}</svg>`;
  }

  // ---------------- reusable bits ----------------
  function pageHead(eyebrow, title, desc, actions, tags) {
    const tagHtml = (tags || []).map((t) => `<span class="chip mono" style="font-size:10px;text-transform:uppercase">${esc(t)}</span>`).join("");
    return `<div class="page-head"><div style="min-width:0">
      <div class="eyebrow">${esc(eyebrow)}</div>
      <h1>${esc(title)}${tagHtml}</h1>
      ${desc ? `<div class="ph-desc">${esc(desc)}</div>` : ""}
    </div>${actions ? `<div class="ph-actions">${actions}</div>` : ""}</div>`;
  }

  function kpi(label, value, accent, sub) {
    return `<div class="card-elev kpi"><div class="kpi-label">${esc(label)}</div>
      <div class="kpi-value" style="color:${accent || "var(--text)"}">${value}</div>
      <div class="kpi-sub">${sub || ""}</div></div>`;
  }

  function statusPill(passed) {
    return passed
      ? `<span class="pill" style="color:var(--success);background:var(--success-soft);border:1px solid #4ade8022">PASS</span>`
      : `<span class="pill" style="color:var(--error);background:var(--error-soft);border:1px solid #f8717122">REGRESSION</span>`;
  }

  // ---------------- views ----------------
  function viewOverview() {
    const passing = SUITES.filter((s) => s.passed).length;
    const regr = SUITES.filter((s) => !s.passed).length;
    const drifting = SUITES.filter((s) => s.drift === "drifting").length;
    const totalCost = COST_BY_NAME.reduce((a, b) => a + b.cost, 0);
    const totalCalls = COST_BY_NAME.reduce((a, b) => a + b.calls, 0);
    const avg = totalCost / totalCalls;
    const tin = COST_BY_NAME.reduce((a, b) => a + b.tokens_in, 0);
    const tout = COST_BY_NAME.reduce((a, b) => a + b.tokens_out, 0);

    const attention = SUITES.filter((s) => !s.passed || s.drift === "drifting").sort((a, b) => a.latest_score - b.latest_score).slice(0, 5);

    const byModule = {};
    COST_BY_NAME.forEach((b) => {
      const m = b.name.split(".")[0];
      byModule[m] = byModule[m] || { module: m, cost: 0, calls: 0 };
      byModule[m].cost += b.cost; byModule[m].calls += b.calls;
    });
    const mods = Object.values(byModule).sort((a, b) => b.cost - a.cost).slice(0, 6);
    const modMax = Math.max(...mods.map((m) => m.cost));

    return pageHead("~/promptry · overview", "Overview", "Eval health and spend at a glance. Dive into Evals or Cost for detail.") +
      `<div class="kpi-row grid-4">
        ${kpi("Suites passing", `${passing}/${SUITES.length}`, "var(--success)", regr > 0 ? `${regr} regressions` : "all green")}
        ${kpi("Drifting", drifting, "var(--warning)", "negative slope")}
        ${kpi("Spend (30d)", usd(totalCost, 2), "var(--text)", `${num(totalCalls)} calls`)}
        ${kpi("Avg $/call", "$" + avg.toFixed(5), "var(--accent)", `${fmtTok(tin)} in · ${fmtTok(tout)} out`)}
      </div>
      <div class="grid-2">
        <div class="card" style="overflow:hidden">
          <div class="card-head"><div class="card-title">Needs attention</div>
            <button class="btn" data-go="prompts">All evals ›</button></div>
          <table class="pr static">${attention.map((s) => `<tr>
            <td><div style="display:flex;align-items:center;gap:8px"><span style="font-weight:600;font-size:13px">${esc(s.name)}</span>${!s.passed ? statusPill(false) : ""}</div></td>
            <td class="c">${sparkline(s.spark, 90, 24)}</td>
            <td class="r mono" style="font-size:14px;font-weight:600;color:${scoreColor(s.latest_score)}">${pct(s.latest_score)}</td>
            <td class="mono" style="color:var(--secondary);font-size:11px">${esc(s.when)}</td></tr>`).join("")}</table>
        </div>
        <div class="card" style="overflow:hidden">
          <div class="card-head"><div class="card-title">Spend by module · 30d</div>
            <button class="btn" data-go="cost">Cost detail ›</button></div>
          <div style="padding:10px 16px">${mods.map((r) => `<div style="margin-bottom:11px">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px">
              <span style="font-size:12.5px;font-family:var(--font-mono);color:var(--text)">${esc(r.module)}<span style="color:var(--muted);margin-left:6px;font-size:11px">${num(r.calls)} calls</span></span>
              <span class="mono" style="font-size:12.5px;color:var(--accent);font-weight:600">$${r.cost.toFixed(2)}</span>
            </div>
            <div class="bar-track"><div class="bar-fill" style="width:${(r.cost / modMax) * 100}%"></div></div></div>`).join("")}</div>
        </div>
      </div>`;
  }

  function viewPrompts() {
    const groups = {};
    PROMPTS.forEach((p) => { const m = p.name.includes(".") ? p.name.split(".")[0] : "other"; (groups[m] = groups[m] || []).push(p); });
    const ordered = Object.keys(groups).sort();
    const body = ordered.map((mod) => {
      const items = groups[mod];
      return `<div style="margin-bottom:18px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding:0 2px">
          <span style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:var(--text);font-family:var(--font-mono)">${esc(mod)}</span>
          <span style="font-size:11px;color:var(--muted);font-family:var(--font-mono)">${items.length}</span>
          <div style="flex:1;height:1px;background:var(--border)"></div>
        </div>
        <div class="card" style="overflow:hidden"><table class="pr">${items.map((p) => {
          const sub = p.name.includes(".") ? p.name.slice(p.name.indexOf(".") + 1) : p.name;
          const clickable = p.name === "rag.qa";
          const tags = p.tags.length ? p.tags.map((t) => `<span class="chip mono" style="font-size:10px;${t === "critical" ? "color:var(--accent);border-color:var(--accent-line);background:var(--accent-soft)" : ""}">${esc(t)}</span>`).join("") : `<span style="color:var(--muted);font-size:11px">—</span>`;
          return `<tr ${clickable ? 'data-prompt="rag.qa"' : 'style="cursor:default"'}>
            <td><div style="font-size:13.5px;color:var(--text);font-weight:600"><span style="color:var(--muted)">${esc(mod)}.</span>${esc(sub)}${clickable ? ' <span class="mono" style="font-size:9.5px;color:var(--accent);margin-left:4px">← open</span>' : ""}</div></td>
            <td class="mono" style="color:var(--text-dim);width:70px">v${p.v}</td>
            <td style="width:200px"><div style="display:flex;gap:6px;flex-wrap:wrap">${tags}</div></td>
            <td class="r" style="width:30px"><span style="color:var(--muted);font-family:var(--font-mono)">›</span></td></tr>`;
        }).join("")}</table></div></div>`;
    }).join("");
    return pageHead("~/promptry · prompts", "Prompt Registry", "Every versioned prompt tracked by promptry. Click rag.qa to inspect history and diffs.",
      `<input class="inp" placeholder="filter prompts…" style="min-width:240px" disabled>`) +
      `<div>${body}</div>`;
  }

  function diffLines(oldStr, newStr) {
    // Minimal line diff: longest-common-subsequence style via naive matching.
    const a = oldStr.split("\n"), b = newStr.split("\n");
    const m = a.length, n = b.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const out = []; let i = 0, j = 0, oi = 1, nj = 1;
    while (i < m && j < n) {
      if (a[i] === b[j]) { out.push({ t: "unchanged", o: oi++, n: nj++, c: a[i] }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "deleted", o: oi++, n: null, c: a[i] }); i++; }
      else { out.push({ t: "added", o: null, n: nj++, c: b[j] }); j++; }
    }
    while (i < m) out.push({ t: "deleted", o: oi++, n: null, c: a[i++] });
    while (j < n) out.push({ t: "added", o: null, n: nj++, c: b[j++] });
    return out;
  }

  const promptState = { sel: 12, tab: "current" };
  function viewPromptDetail() {
    const vers = RAGQA_VERSIONS;
    const cur = vers.find((v) => v.v === promptState.sel) || vers[0];
    const idx = vers.findIndex((v) => v.v === cur.v);
    const prev = vers[idx + 1];
    const isLatest = cur.v === vers[0].v;
    const prodV = vers.find((v) => v.tags.includes("prod"));

    const tab = (id, label, disabled) =>
      `<button class="tab ${promptState.tab === id ? "active" : ""}" ${disabled ? "disabled style=\"opacity:.5;cursor:not-allowed\"" : `data-tab="${id}"`}>${label}</button>`;

    const rail = vers.map((v, i) => `<div class="ver-item ${cur.v === v.v ? "sel" : ""}" data-ver="${v.v}">
      <div class="ver-head"><span class="ver-v">v${v.v}${i === 0 ? '<span class="ver-tag" style="color:var(--success)">LATEST</span>' : ""}${v.tags.includes("prod") ? '<span class="ver-tag" style="color:var(--accent)">PROD</span>' : ""}</span>
        <span class="ver-hash">${esc(v.hash.slice(0, 7))}</span></div>
      <div class="ver-when">${esc(v.when)}</div></div>`).join("");

    // render content with $variables as pills
    const renderContent = (txt) => esc(txt).replace(/\$\{?([A-Za-z_]\w*)\}?/g, (mm, name) => `<span class="varpill">$${name}</span>`);

    let panel = "";
    if (promptState.tab === "current") {
      panel = `<pre class="code">${renderContent(cur.content)}</pre>`;
    } else if (promptState.tab === "diff") {
      if (!prev) panel = `<div style="padding:24px;color:var(--muted);font-size:12px;text-align:center">Earliest version — no diff available.</div>`;
      else {
        const lines = diffLines(prev.content, cur.content);
        panel = `<div style="padding:8px 0">${lines.map((l) => `<div class="diff-line ${l.t}">
          <span class="ln">${l.o || ""}</span><span class="ln">${l.n || ""}</span>
          <pre style="color:${l.t === "added" ? "var(--success)" : l.t === "deleted" ? "var(--error)" : "var(--text-dim)"}">${l.t === "added" ? "+ " : l.t === "deleted" ? "- " : "  "}${esc(l.c) || " "}</pre></div>`).join("")}</div>`;
      }
    } else if (promptState.tab === "stats") {
      panel = statsPanel();
    } else if (promptState.tab === "calls") {
      panel = `<div style="padding:16px">${invocationsTable(RAGQA_INVOCATIONS)}</div>`;
    }

    const diffBadges = (promptState.tab === "diff" && prev)
      ? (() => { const lines = diffLines(prev.content, cur.content); const add = lines.filter((l) => l.t === "added").length, del = lines.filter((l) => l.t === "deleted").length;
        return `<span class="chip mono" style="font-size:10px;color:var(--error)">− ${del}</span><span class="chip mono" style="font-size:10px;color:var(--success)">+ ${add}</span>`; })()
      : "";

    const promote = (cur === prodV)
      ? `<span class="chip mono" style="font-size:10px;color:var(--accent);border-color:var(--accent-line);background:var(--accent-soft)">serving in prod</span>`
      : `<button class="btn" data-promote="${cur.v}" style="color:#1a0e04;background:var(--accent);border:none;font-weight:600;font-size:11.5px">Promote v${cur.v} → prod</button>`;

    return `<div class="crumbs"><a data-go="prompts">prompts</a><span>/</span><span class="cur">rag.qa</span></div>` +
      pageHead("Prompt", "rag.qa", `${vers.length} versions tracked.`) +
      `<div style="display:grid;grid-template-columns:240px 1fr;gap:12px">
        <div class="card" style="padding:8px;height:fit-content">
          <div style="padding:6px 8px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;font-family:var(--font-mono)">Versions</div>${rail}</div>
        <div class="card" style="padding:0">
          <div style="padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:8px">
            <div class="tabs">${tab("current", "Current · v" + cur.v)}${tab("diff", "Diff", !prev)}${tab("stats", "Stats")}${tab("calls", "Invocations")}</div>
            <div style="display:flex;gap:8px;align-items:center">${diffBadges}${promote}</div>
          </div>
          ${panel}
        </div>
      </div>`;
  }

  function statsPanel() {
    const cards = [
      ["calls (30d)", "24,210"], ["avg cost / call", "$0.00040"], ["total cost", "$9.74"],
      ["avg input tok", "500"], ["avg output tok", "60"], ["avg latency", "910ms"],
    ];
    const rows = [
      ["Input tokens (sent, incl. payload)", "180", "470", "500", "1,240", "12,400"],
      ["Output tokens (response)", "40", "55", "60", "120", "640"],
      ["Cost", "$0.00005", "$0.00030", "$0.00040", "$0.00120", "$0.03740"],
      ["Latency", "610ms", "820ms", "910ms", "1.6s", "2.2s"],
    ];
    const hist = [3, 9, 22, 41, 58, 67, 52, 38, 24, 12, 6, 3];
    const maxBar = Math.max(...hist);
    const starts = [180, 1200, 2200, 3200, 4200, 5200, 6200, 7200, 8200, 9200, 10200, 11200];
    return `<div style="padding:16px">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:18px">
        ${cards.map((c) => `<div style="padding:10px 12px;background:var(--bg-elev);border:1px solid var(--border);border-radius:8px">
          <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;font-family:var(--font-mono)">${c[0]}</div>
          <div style="font-size:18px;font-weight:700;color:var(--text);margin-top:3px">${c[1]}</div></div>`).join("")}
      </div>
      <table class="pr static" style="margin-bottom:18px"><thead><tr><th>Metric</th><th class="r">min</th><th class="r">p50</th><th class="r">avg</th><th class="r">p95</th><th class="r">max</th></tr></thead>
        <tbody>${rows.map((r) => `<tr><td style="font-size:12.5px;color:var(--text-dim)">${r[0]}</td>${r.slice(1).map((v, i) => `<td class="r mono" style="font-size:12px;${i === 2 ? "color:var(--text)" : ""}">${v}</td>`).join("")}</tr>`).join("")}</tbody></table>
      <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;font-family:var(--font-mono);margin-bottom:8px">Input size distribution (tokens / call)</div>
      <div style="display:flex;align-items:flex-end;gap:3px;height:120px">${hist.map((c, i) => `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px" title="${starts[i]} tok: ${c} calls">
        <div style="width:100%;height:${(c / maxBar) * 96}px;background:var(--accent);border-radius:3px 3px 0 0;opacity:0.85"></div>
        <span style="font-size:8.5px;color:var(--muted);font-family:var(--font-mono)">${starts[i]}</span></div>`).join("")}</div>
    </div>`;
  }

  function ratingDot(r) {
    if (r == null) return `<span style="color:var(--muted)">—</span>`;
    return r > 0 ? `<span style="color:var(--success)">★ ↑</span>` : `<span style="color:var(--error)">★ ↓</span>`;
  }
  function invocationsTable(rows) {
    return `<div class="card" style="overflow:hidden"><table class="pr"><thead><tr>
      <th>#</th><th>Model</th><th class="r">Tok in</th><th class="r">Tok out</th><th class="r">Cost</th><th class="r">Latency</th><th class="c">Rating</th><th>Output preview</th></tr></thead>
      <tbody>${rows.map((r) => `<tr data-invocation="${r.id}">
        <td class="mono" style="color:var(--accent)">#${r.id}</td>
        <td class="mono" style="color:var(--text-dim);font-size:12px">${esc(r.model)}</td>
        <td class="r mono" style="color:var(--text-dim)">${num(r.tokens_in)}</td>
        <td class="r mono" style="color:var(--text-dim)">${num(r.tokens_out)}</td>
        <td class="r mono" style="color:var(--accent);font-weight:600">${usd(r.cost)}</td>
        <td class="r mono" style="color:var(--text-dim)">${r.latency}ms</td>
        <td class="c">${ratingDot(r.rating)}</td>
        <td class="mono" style="color:var(--secondary);font-size:11px;max-width:280px;overflow:hidden;text-overflow:ellipsis">${esc(r.preview)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // ----- COST drill-down -----
  const costState = { module: null, prompt: null };
  function viewCost() {
    const moduleOf = (n) => (n.includes(".") ? n.split(".")[0] : "other");
    const short = (p) => p.slice(p.indexOf(".") + 1);
    const totalCost = COST_BY_NAME.reduce((a, b) => a + b.cost, 0);
    const totalCalls = COST_BY_NAME.reduce((a, b) => a + b.calls, 0);
    const tin = COST_BY_NAME.reduce((a, b) => a + b.tokens_in, 0);
    const tout = COST_BY_NAME.reduce((a, b) => a + b.tokens_out, 0);
    const overallHit = COST_BY_NAME.reduce((a, b) => a + b.cache_hit * b.tokens_in, 0) / tin;

    const head = pageHead("~/promptry · cost", "Cost & Tokens",
      "Follow the money: module spend → prompt → the priciest individual calls.",
      `<select class="inp" disabled><option>Last 14 days</option></select>`);

    // breadcrumb
    let crumbs = "";
    if (costState.module || costState.prompt) {
      crumbs = `<div class="crumbs"><a data-cost-level="overview">cost</a>`;
      if (costState.module) crumbs += `<span>/</span>` + (costState.prompt ? `<a data-cost-module="${esc(costState.module)}">${esc(costState.module)}</a>` : `<span class="cur">${esc(costState.module)}</span>`);
      if (costState.prompt) crumbs += `<span>/</span><span class="cur">${esc(short(costState.prompt))}</span>`;
      crumbs += `</div>`;
    }

    // ---- prompt level ----
    if (costState.prompt) {
      const p = COST_BY_NAME.find((x) => x.name === costState.prompt);
      const calls = RAGQA_INVOCATIONS; // demo: reuse for rag.qa; otherwise synth a small set
      const isRag = costState.prompt === "rag.qa";
      const rows = isRag ? calls : [
        { id: 88120, model: p.models[0], tokens_in: Math.round(p.tokens_in / p.calls * 2), tokens_out: 380, cost: p.cost / p.calls * 3, latency: 1900, when: "1h ago", rating: null, preview: "…" },
        { id: 88090, model: p.models[0], tokens_in: Math.round(p.tokens_in / p.calls * 1.6), tokens_out: 300, cost: p.cost / p.calls * 2.2, latency: 1500, when: "2h ago", rating: 1, preview: "…" },
      ];
      return crumbs + head +
        `<div class="kpi-row grid-4">
          ${kpi("Total spend", usd(p.cost, 2), "var(--accent)", `${num(p.calls)} calls (14d)`)}
          ${kpi("Avg $/call", "$" + (p.cost / p.calls).toFixed(5), "var(--text)", "")}
          ${kpi("p95 $/call", usd(p.cost / p.calls * 4, 5), "var(--warning)", "tail cost")}
          ${kpi("Max $/call", usd(Math.max(...rows.map((r) => r.cost)), 5), "var(--error)", "priciest single call")}
        </div>
        <div style="font-size:12.5px;color:var(--muted);margin-bottom:8px">Most expensive calls first — click one to see exactly what drove the cost.</div>
        ${invocationsTable(rows.slice().sort((a, b) => b.cost - a.cost))}`;
    }

    // ---- module level ----
    if (costState.module) {
      const prompts = COST_BY_NAME.filter((b) => moduleOf(b.name) === costState.module).sort((a, b) => b.cost - a.cost);
      const max = Math.max(...prompts.map((p) => p.cost));
      const tc = prompts.reduce((a, b) => a + b.cost, 0), tcalls = prompts.reduce((a, b) => a + b.calls, 0);
      return crumbs + head +
        `<div class="kpi-row grid-3">
          ${kpi(costState.module + " spend", usd(tc, 2), "var(--accent)", `${num(tcalls)} calls`)}
          ${kpi("Prompts", prompts.length, "var(--text)", "in this module")}
          ${kpi("Avg $/call", "$" + (tc / tcalls).toFixed(5), "var(--text-dim)", "")}
        </div>
        <div class="card" style="overflow:hidden"><div class="card-head"><div class="card-title">Prompts by spend</div></div>
          <table class="pr"><thead><tr><th>Prompt</th><th class="r">Calls</th><th class="r">Tokens in</th><th class="r">Tokens out</th><th>Share</th><th class="r">Cost</th><th class="r"></th></tr></thead>
          <tbody>${prompts.map((p) => `<tr data-cost-prompt="${esc(p.name)}">
            <td style="font-weight:600;font-size:13px"><span style="color:var(--muted)">${esc(costState.module)}.</span>${esc(short(p.name))}</td>
            <td class="r mono" style="color:var(--text-dim)">${num(p.calls)}</td>
            <td class="r mono" style="color:var(--text-dim)">${fmtTok(p.tokens_in)}</td>
            <td class="r mono" style="color:var(--text-dim)">${fmtTok(p.tokens_out)}</td>
            <td><div class="bar-track" style="width:90px"><div class="bar-fill" style="width:${(p.cost / max) * 100}%"></div></div></td>
            <td class="r mono" style="color:var(--accent);font-weight:600">$${p.cost.toFixed(2)}</td>
            <td class="r"><span style="color:var(--muted);font-family:var(--font-mono)">›</span></td></tr>`).join("")}</tbody></table></div>`;
    }

    // ---- overview level ----
    const byModule = {};
    COST_BY_NAME.forEach((b) => { const m = moduleOf(b.name); byModule[m] = byModule[m] || { module: m, calls: 0, tokens_in: 0, cost: 0 }; const e = byModule[m]; e.calls += b.calls; e.tokens_in += b.tokens_in; e.cost += b.cost; });
    const mods = Object.values(byModule).sort((a, b) => b.cost - a.cost);
    const modMax = Math.max(...mods.map((r) => r.cost));

    const coverageBanner = COVERAGE.uncosted.length
      ? `<div style="margin-bottom:16px;padding:11px 14px;border-radius:8px;border:1px solid var(--warning);background:color-mix(in oklch, var(--warning) 12%, transparent);font-size:12.5px;color:var(--text-dim)">
          <span style="color:var(--warning);font-weight:600">${COVERAGE.uncosted.length} model(s) with no pricing</span> — ${num(COVERAGE.uncosted_calls)} calls counted as $0. Missing: <span class="mono">${COVERAGE.uncosted.map((m) => esc(m.model)).join(", ")}</span>.</div>`
      : "";

    const budgetsPanel = `<div class="card" style="overflow:hidden;margin-bottom:20px">
      <div class="card-head"><div><div class="card-title">Budgets</div><div class="card-sub">Spend caps per period — breaches highlight in red.</div></div>
        <button class="btn" disabled>+ Budget</button></div>
      <div style="padding:8px 16px 12px">${BUDGETS.map((b) => {
        const label = b.scope === "global" ? "All prompts" : `${b.scope}: ${b.target}`;
        const barColor = b.breached ? "var(--error)" : b.pct >= 80 ? "var(--warning)" : "var(--accent)";
        return `<div style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:12.5px">
            <span><span style="font-weight:600">${esc(label)}</span> <span style="color:var(--muted);font-size:11px">· ${b.period}</span></span>
            <span class="mono" style="color:${b.breached ? "var(--error)" : "var(--text-dim)"}">$${b.spend.toFixed(2)} / $${b.limit_usd.toFixed(2)} (${b.pct.toFixed(0)}%)</span>
          </div>
          <div class="bar-track tall"><div class="bar-fill" style="width:${Math.min(100, b.pct)}%;background:${barColor};opacity:1"></div></div>
          ${b.breached ? `<div style="font-size:11px;color:var(--error);margin-top:3px">Over budget by $${(b.spend - b.limit_usd).toFixed(2)}.</div>` : ""}</div>`;
      }).join("")}</div></div>`;

    return crumbs + head + coverageBanner +
      `<div class="kpi-row grid-4">
        ${kpi("Total spend", usd(totalCost, 2), "var(--text)", `${num(totalCalls)} calls`)}
        ${kpi("Avg $/call", "$" + (totalCost / totalCalls).toFixed(5), "var(--accent)", `${fmtTok(tin)} in · ${fmtTok(tout)} out`)}
        ${kpi("Cache hit rate", pct(overallHit, 1), "var(--success)", "saved ~$4.10")}
        ${kpi("Modules", mods.length, "var(--text-dim)", "grouped by prefix")}
      </div>
      <div class="card" style="padding:16px;margin-bottom:20px"><div class="card-title" style="margin-bottom:12px">Daily spend</div>${barChart(COST_BY_DATE, "cost", "date", 200)}</div>
      ${budgetsPanel}
      <div class="card" style="overflow:hidden"><div class="card-head"><div><div class="card-title">By module</div><div class="card-sub">Click a module to drill into its prompts.</div></div></div>
        <table class="pr"><thead><tr><th>Module</th><th class="r">Calls</th><th class="r">Tokens in</th><th>Share</th><th class="r">Cost</th><th class="r"></th></tr></thead>
        <tbody>${mods.map((r) => `<tr data-cost-module="${esc(r.module)}">
          <td style="font-weight:700;font-family:var(--font-mono);font-size:13px">${esc(r.module)}</td>
          <td class="r mono" style="color:var(--text-dim)">${num(r.calls)}</td>
          <td class="r mono" style="color:var(--text-dim)">${fmtTok(r.tokens_in)}</td>
          <td><div class="bar-track" style="width:90px"><div class="bar-fill" style="width:${(r.cost / modMax) * 100}%"></div></div></td>
          <td class="r mono" style="color:var(--accent);font-weight:600">$${r.cost.toFixed(2)}</td>
          <td class="r"><span style="color:var(--muted);font-family:var(--font-mono)">›</span></td></tr>`).join("")}</tbody></table></div>`;
  }

  // ----- INVOCATION detail -----
  function viewInvocation() {
    const d = INVOCATION;
    const meta = [["model", d.model], ["tokens in", num(d.tokens_in)], ["tokens out", num(d.tokens_out)], ["cost", usd(d.cost)], ["latency", d.latency + "ms"]];
    const total = d.template_tokens + d.data_tokens;
    const tPct = (d.template_tokens / total) * 100;
    const split = (color, label, tok, cost) => `<div><div style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)"><span class="split-swatch" style="background:${color}"></span>${label}</div>
      <div style="font-size:14px;font-weight:600;color:var(--text);margin-top:3px">${num(tok)} tok <span style="color:var(--accent);font-weight:500;font-size:12.5px">${usd(cost)}</span></div></div>`;
    const block = (label, text, color) => `<div class="card" style="padding:0;overflow:hidden">
      <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;font-family:var(--font-mono)">${label}</div>
      <pre style="margin:0;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-word;color:${color};padding:14px;max-height:340px;overflow:auto;font-family:var(--font-mono)">${esc(text)}</pre></div>`;

    return `<div class="crumbs"><a data-go="cost">cost</a><span>/</span><a data-cost-drill="rag.qa">rag.qa</a><span>/</span><span class="cur">invocation #${d.id}</span></div>` +
      pageHead("~/promptry · invocation", "#" + d.id, `${d.prompt_name} · v${d.prompt_version} · ${d.when}`,
        `<button class="btn" data-go="cost">← Back</button>`) +
      `<div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:18px">${meta.map((kv) => `<div>
        <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;font-family:var(--font-mono)">${kv[0]}</div>
        <div style="font-size:15px;font-weight:600;color:var(--text);margin-top:2px">${kv[1]}</div></div>`).join("")}</div>

      <div class="card" style="padding:16px;margin-bottom:18px">
        <div class="card-title">Where the input cost went <span style="font-size:11px;color:var(--muted);font-weight:400">· estimated (~4 chars/token)</span></div>
        <div style="font-size:11.5px;color:var(--muted);margin-top:3px;margin-bottom:12px">How much of this call is fixed template overhead vs the variable payload you fed in.</div>
        <div class="split-bar"><div style="width:${tPct}%;background:var(--accent)" title="template"></div><div style="width:${100 - tPct}%;background:var(--text-dim);opacity:0.4" title="payload"></div></div>
        <div class="split-legend">${split("var(--accent)", "Template (fixed)", d.template_tokens, d.template_cost)}${split("var(--text-dim)", "Payload (variable)", d.data_tokens, d.data_cost)}${split("var(--success)", "Response", d.tokens_out_n, d.output_cost)}</div>
      </div>

      <div class="card" style="padding:16px;margin-bottom:18px">
        <div class="card-title" style="margin-bottom:8px">Feedback</div>
        ${d.feedback.map((f) => `<div style="font-size:12.5px;color:var(--text-dim);margin-bottom:6px;padding:8px 11px;background:var(--bg-elev);border-radius:6px">
          <span style="color:var(--error);font-weight:600;margin-right:8px">★ ↓</span>${esc(f.comment)}<span style="color:var(--muted);margin-left:8px;font-size:10.5px">via ${esc(f.source)} · ${esc(f.when)}</span></div>`).join("")}
      </div>

      ${block("Request", d.input_text, "var(--text-dim)")}
      <div style="height:14px"></div>
      ${block("Response", d.output_text, "var(--text)")}

      <div style="margin-top:14px;font-size:12px"><a data-prompt="rag.qa" style="color:var(--accent);cursor:pointer">View prompt · rag.qa →</a></div>`;
  }

  // ----- PLAYGROUND -----
  const PG_MODELS = [
    { id: "gpt-4o", label: "gpt-4o", inC: 2.5, outC: 10 },
    { id: "gpt-4o-mini", label: "gpt-4o-mini", inC: 0.15, outC: 0.6 },
    { id: "claude-haiku-4-5", label: "claude-haiku-4-5", inC: 0.25, outC: 1.25 },
    { id: "claude-sonnet-4-5", label: "claude-sonnet-4-5", inC: 3, outC: 15 },
    { id: "llama-3.3-70b", label: "llama-3.3-70b", inC: 0.59, outC: 0.79 },
  ];
  // Pre-baked synthetic "runs" so the playground shows results without a backend.
  const PG_RESULTS = {
    "gpt-4o-mini": { latency: 740, tin: 142, tout: 38, cost: 0.0001, score: 1.0, pass: 5, total: 5,
      body: '{"answer":"100 requests per minute","sources":["billing.md#rate-limits"]}',
      asserts: [["contains","100 requests",true,1.0,"1/1 keywords found"],["json_valid","",true,1.0,"parses as JSON"],["not_contains","unlimited, unsure",true,1.0,"no banned terms"],["json_path_eq","sources[0]=billing.md#rate-limits",true,1.0,"match"],["max_tokens","120",true,1.0,"31 / 120 token budget"]] },
    "claude-haiku-4-5": { latency: 910, tin: 142, tout: 54, cost: 0.0001, score: 0.74, pass: 3, total: 5,
      body: 'The free plan allows 100 requests per minute (10k/day). Source: billing.md.',
      asserts: [["contains","100 requests",true,1.0,"1/1 keywords found"],["json_valid","",false,0.0,"JSON parse error"],["not_contains","unlimited, unsure",true,1.0,"no banned terms"],["json_path_eq","sources[0]=billing.md#rate-limits",false,0.0,"could not parse/path"],["max_tokens","120",true,1.0,"22 / 120 token budget"]] },
  };
  const pgState = { models: ["gpt-4o-mini", "claude-haiku-4-5"], ran: false, focus: "gpt-4o-mini", temp: 0.2 };

  function viewPlayground() {
    const sys = 'You are a careful billing support assistant. Answer using ONLY the provided context. If the answer isn\'t supported, reply "I don\'t know.". Return JSON: {answer, sources}.';
    const user = "What is the rate limit on the {{plan}} plan?";
    const context = "# billing.md\n## rate-limits\n- free: 100 req/min, 10k/day\n- pro: 1000 req/min, 250k/day\n- burst requests over the limit return HTTP 429";
    const rules = [["Must contain", "100 requests", 1.0], ["Valid JSON", "", 0.5], ["Must NOT contain", "unlimited, unsure", 1.0], ["JSON field equals", "sources[0]=billing.md#rate-limits", 1.5], ["Max tokens ≤", "120", 0.5]];

    const head = pageHead("~/promptry · playground", "Prompt Playground",
      "Iterate on a prompt, try it across models, and preview assertion results before promoting to a suite.",
      `<button class="btn btn-primary" data-pg-run>Run <span class="kbd" style="margin-left:4px;background:rgba(26,14,4,0.2);border-color:rgba(26,14,4,0.3);color:#1a0e04">⌘↵</span></button>`,
      ["billing/rate_limit", pgState.models.length + " models", rules.length + " assertions"]);

    const presetStrip = `<div class="card" style="padding:10px;margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;font-family:var(--font-mono);margin-right:4px">Presets</span>
      <button class="btn" style="border-color:var(--accent-line);background:var(--accent-soft);color:var(--accent);font-family:var(--font-mono);font-size:11.5px">billing/rate_limit</button>
      <button class="btn" style="font-family:var(--font-mono);font-size:11.5px;color:var(--text-dim)">support/refund_policy</button>
      <button class="btn" style="font-family:var(--font-mono);font-size:11.5px;color:var(--text-dim)">intent/classifier</button>
      <div style="flex:1"></div><span style="font-size:11.5px;color:var(--muted)">RAG answer about free-tier rate limiting</span></div>`;

    const labeled = (lbl, right, body) => `<div><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
      <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;font-family:var(--font-mono)">${lbl}</div>
      ${right ? `<span class="mono" style="font-size:10px;color:var(--muted)">${right}</span>` : ""}</div>${body}</div>`;

    const editor = `<div class="card" style="padding:0;overflow:hidden">
      <div style="display:flex;border-bottom:1px solid var(--border);background:var(--bg-elev)">
        <button class="btn" style="border-radius:0;border-bottom:2px solid var(--accent);background:transparent;border-top:none;border-left:none;border-right:none;color:var(--text);font-weight:600;padding:10px 14px;font-size:12.5px">Prompt <span class="mono" style="font-size:10.5px;color:var(--muted)">${sys.length + user.length}</span></button>
        <button class="btn" style="border-radius:0;background:transparent;border:none;color:var(--secondary);padding:10px 14px;font-size:12.5px">Context <span class="mono" style="font-size:10.5px;color:var(--muted)">${context.length}</span></button>
        <button class="btn" style="border-radius:0;background:transparent;border:none;color:var(--secondary);padding:10px 14px;font-size:12.5px">Assertions <span class="mono" style="font-size:10.5px;color:var(--muted)">${rules.length}</span></button>
      </div>
      <div style="padding:14px;display:flex;flex-direction:column;gap:12px">
        ${labeled("System", sys.length + " chars", `<textarea class="inp" rows="4" style="width:100%" readonly>${esc(sys)}</textarea>`)}
        ${labeled("User message", 'use {{name}} for variables', `<textarea class="inp" rows="2" style="width:100%" readonly>${esc(user)}</textarea>`)}
        <div>
          <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;font-family:var(--font-mono)">Variables</div>
          <div style="display:flex;align-items:center;gap:8px;background:var(--bg-elev);border:1px solid var(--border);border-radius:6px;padding:4px 8px;max-width:240px">
            <span class="mono" style="font-size:11px;color:var(--accent)">plan</span><span style="color:var(--muted)">=</span>
            <input class="inp" style="flex:1;min-width:0;padding:2px 6px;border:none;background:transparent" value="free" readonly></div>
          <div style="margin-top:8px;padding:8px;background:var(--bg-elev);border:1px dashed var(--border-strong);border-radius:4px;font-family:var(--font-mono);font-size:11.5px;color:var(--text-dim)"><span style="color:var(--muted)">resolves → </span>What is the rate limit on the free plan?</div>
        </div>
        <div style="display:flex;gap:18px;padding-top:6px;border-top:1px solid var(--border)">
          <label style="display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--secondary)">
            <span class="mono" style="font-size:10px;text-transform:uppercase;letter-spacing:0.06em">temp</span>
            <input type="range" min="0" max="1" step="0.05" value="${pgState.temp}" style="accent-color:var(--accent);width:110px" disabled>
            <span class="mono" style="color:var(--text);width:30px">${pgState.temp.toFixed(2)}</span></label>
        </div>
      </div></div>`;

    const modelSel = `<div class="card" style="padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;font-family:var(--font-mono)">Models (${pgState.models.length})</div>
        <span style="font-size:11px;color:var(--muted)">Select 1–5 to compare</span></div>
      <div style="display:flex;flex-direction:column;gap:6px">${PG_MODELS.map((m) => {
        const on = pgState.models.includes(m.id);
        return `<label class="model-row ${on ? "on" : ""}" data-pg-model="${m.id}">
          <input type="checkbox" ${on ? "checked" : ""} style="accent-color:var(--accent)">
          <span class="mono" style="font-size:12.5px;color:${on ? "var(--text)" : "var(--text-dim)"};font-weight:600;flex:1">${m.label}</span>
          <span class="mono" style="font-size:10.5px;color:var(--muted)">$${m.inC.toFixed(2)}/M in</span>
          <span class="mono" style="font-size:10.5px;color:var(--muted)">$${m.outC.toFixed(2)}/M out</span></label>`;
      }).join("")}</div></div>`;

    // right side: results
    let right = "";
    if (!pgState.ran) {
      right = `<div class="card" style="padding:40px;text-align:center;color:var(--muted);border:1px dashed var(--border-strong);background:transparent">
        <svg width="40" height="40" viewBox="0 0 40 40" style="margin-bottom:12px;opacity:0.5"><rect x="2" y="2" width="36" height="36" rx="8" stroke="var(--border-strong)" stroke-width="1.5" fill="none" stroke-dasharray="3 3"/><path d="M14 20 L18 24 L26 16" stroke="var(--accent)" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <div style="font-size:13px;color:var(--text-dim);margin-bottom:4px">No runs yet</div>
        <div style="font-size:11.5px">Click <span class="kbd">⌘</span> <span class="kbd">↵</span> to run across ${pgState.models.length} models</div></div>`;
    } else {
      right = pgState.models.map((mId) => {
        const m = PG_MODELS.find((x) => x.id === mId);
        const r = PG_RESULTS[mId] || PG_RESULTS["gpt-4o-mini"];
        const focused = pgState.focus === mId;
        return `<div class="card ${r ? "enter" : ""}" data-pg-focus="${mId}" style="padding:0;overflow:hidden;${focused ? "border-color:var(--accent-line);box-shadow:0 0 0 1px var(--accent-line)" : ""}">
          <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--bg-elev);border-bottom:1px solid var(--border);cursor:pointer">
            <span class="mono" style="font-size:12.5px;font-weight:600;color:var(--text);flex:1">${m.label}</span>
            <span class="mono" style="font-size:10.5px;color:var(--muted)">${r.latency}ms</span>
            <span class="mono" style="font-size:10.5px;color:var(--muted)">${r.tin}→${r.tout}</span>
            <span class="mono" style="font-size:10.5px;color:var(--muted)">$${r.cost.toFixed(4)}</span>
            <span class="mono" style="font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;background:${r.pass === r.total ? "var(--success-soft)" : "var(--error-soft)"};color:${r.pass === r.total ? "var(--success)" : "var(--error)"}">${Math.round(r.score * 100)}% · ${r.pass}/${r.total}</span>
          </div>
          <div style="padding:12px 14px;display:flex;flex-direction:column;gap:10px">
            <pre class="mono" style="margin:0;font-size:12.5px;color:var(--text-dim);white-space:pre-wrap;line-height:1.6;background:var(--bg);padding:12px;border-radius:6px;border:1px solid var(--border);max-height:${focused ? 320 : 120}px;overflow:auto">${esc(r.body)}</pre>
            <div class="assert-bar">${r.asserts.map((a) => `<div style="flex:${a[3] != null ? 1 : 1};background:${a[2] ? "var(--success)" : "var(--error)"};opacity:${0.3 + a[3] * 0.7}" title="${esc(a[0])}: ${esc(a[4])}"></div>`).join("")}</div>
            ${focused ? `<div style="display:flex;flex-direction:column;gap:4px">${r.asserts.map((a) => `<div class="assert-row" style="background:${a[2] ? "var(--success-soft)" : "var(--error-soft)"};border:1px solid ${a[2] ? "rgba(74,222,128,0.18)" : "rgba(248,113,113,0.18)"}">
              <span class="mono" style="font-size:10.5px;font-weight:700;color:${a[2] ? "var(--success)" : "var(--error)"}">${a[2] ? "PASS" : "FAIL"}</span>
              <span style="font-family:var(--font-mono);color:var(--text-dim);font-size:11.5px;overflow:hidden;text-overflow:ellipsis">${esc(a[0])}${a[1] ? `<span style="color:var(--muted)"> · ${esc(a[1])}</span>` : ""}</span>
              <span style="font-size:10.5px;color:var(--muted);font-style:italic">${esc(a[4])}</span>
              <span class="mono" style="font-size:11px;color:${a[2] ? "var(--success)" : "var(--error)"};font-weight:600;width:36px;text-align:right">${Math.round(a[3] * 100)}%</span></div>`).join("")}</div>` : ""}
          </div></div>`;
      }).join("");

      // comparison table
      const ranked = pgState.models.map((id) => ({ id, r: PG_RESULTS[id] || PG_RESULTS["gpt-4o-mini"] })).sort((a, b) => b.r.score - a.r.score);
      right += `<div class="card enter" style="padding:14px">
        <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;font-family:var(--font-mono)">Comparison</div>
        <table class="pr static" style="font-size:12px"><thead><tr><th>Model</th><th class="r">Score</th><th class="r">Latency</th><th class="r">Cost</th><th class="r">Tokens</th></tr></thead>
        <tbody>${ranked.map((x, i) => `<tr><td style="font-family:var(--font-mono)">${i === 0 ? '<span class="mono" style="color:var(--accent);margin-right:6px">▸</span>' : ""}${esc(PG_MODELS.find((m) => m.id === x.id).label)}</td>
          <td class="r mono" style="color:${x.r.pass === x.r.total ? "var(--success)" : "var(--error)"};font-weight:600">${Math.round(x.r.score * 100)}%</td>
          <td class="r mono" style="color:var(--text-dim)">${x.r.latency}ms</td>
          <td class="r mono" style="color:var(--text-dim)">$${x.r.cost.toFixed(4)}</td>
          <td class="r mono" style="color:var(--muted)">${x.r.tin}→${x.r.tout}</td></tr>`).join("")}</tbody></table></div>`;
    }

    const rulesNote = `<div style="margin-top:12px;font-size:11px;color:var(--muted);font-family:var(--font-mono)">assertions: ${rules.map((r) => esc(r[0])).join(" · ")}</div>`;

    return head + presetStrip +
      `<div class="pg-grid">
        <div style="display:flex;flex-direction:column;gap:12px;min-width:0">${editor}${modelSel}${rulesNote}</div>
        <div style="display:flex;flex-direction:column;gap:12px;min-width:0" id="pg-right">${right}</div>
      </div>`;
  }

  // ---------------- router ----------------
  let current = "overview";
  const VIEWS = {
    overview: viewOverview,
    prompts: () => (promptOpen ? viewPromptDetail() : viewPrompts()),
    cost: viewCost,
    invocation: viewInvocation,
    playground: viewPlayground,
  };
  let promptOpen = false;

  function renderNav() {
    return NAV.map((n) => `<button class="sn-item ${current === n.id ? "active" : ""}" data-nav="${n.id}">
      <svg viewBox="0 0 16 16">${ICONS[n.id] || ICONS.overview}</svg><span class="sn-label">${n.label}</span>
      ${n.id === "overview" ? '<span class="sn-badge">2</span>' : ""}</button>`).join("");
  }

  function render() {
    const root = document.getElementById("app");
    const banner = `<div class="demo-banner">
      <b>DEMO</b> <span>Synthetic data — this is a static replica of the promptry dashboard. Nothing here is wired to a backend.</span>
      <a class="demo-back" href="../index.html">← back to promptry.dev</a></div>`;
    root.innerHTML = `
      <div class="app">
        <aside class="sidenav">
          <div class="sn-brand">
            <a class="wordmark" href="../index.html">
              <svg viewBox="0 0 32 32" width="22" height="22"><rect width="32" height="32" rx="7" fill="#1a1a1e"/><text x="0" y="27" font-family="system-ui,sans-serif" font-weight="700" font-size="28" fill="#e8e8ec">p</text><text x="15" y="27" font-family="system-ui,sans-serif" font-weight="700" font-size="28" fill="oklch(0.78 0.14 82)">r</text></svg>
              <span class="wm-name">prompt<span class="ry">ry</span></span></a>
            <span class="chip mono sn-ver">v0.9.5</span>
          </div>
          <button class="sn-search"><svg width="13" height="13" viewBox="0 0 16 16" fill="none" style="color:var(--secondary)"><circle cx="7" cy="7" r="5" stroke="currentColor" stroke-width="1.4"/><path d="m11 11 3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg><span style="color:var(--secondary)">Search…</span><span class="kbd">⌘K</span></button>
          <div class="sn-group-label">Workspace</div>
          ${renderNav()}
          <div class="sn-conn"><span class="dot"></span><span class="host">localhost:8420</span><span class="v">v0.9.5</span></div>
        </aside>
        <main class="main">${banner}<div id="view">${VIEWS[current]()}</div></main>
      </div>`;
    bind();
  }

  function setView(id) { current = id; if (id !== "prompts") promptOpen = false; window.scrollTo(0, 0); render(); }

  function bind() {
    document.querySelectorAll("[data-nav]").forEach((el) => el.addEventListener("click", () => setView(el.dataset.nav)));
    document.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => setView(el.dataset.go)));

    // prompts list → detail
    document.querySelectorAll("[data-prompt]").forEach((el) => el.addEventListener("click", () => { promptOpen = true; promptState.sel = 12; promptState.tab = "current"; setView("prompts"); }));

    // prompt detail interactions
    document.querySelectorAll("[data-ver]").forEach((el) => el.addEventListener("click", () => { promptState.sel = Number(el.dataset.ver); rerenderView(); }));
    document.querySelectorAll("[data-tab]").forEach((el) => el.addEventListener("click", () => { promptState.tab = el.dataset.tab; rerenderView(); }));
    document.querySelectorAll("[data-promote]").forEach((el) => el.addEventListener("click", () => {
      const v = Number(el.dataset.promote);
      RAGQA_VERSIONS.forEach((x) => { x.tags = x.tags.filter((t) => t !== "prod"); });
      const tgt = RAGQA_VERSIONS.find((x) => x.v === v); if (tgt && !tgt.tags.includes("prod")) tgt.tags.push("prod");
      rerenderView();
    }));

    // cost drill
    document.querySelectorAll("[data-cost-module]").forEach((el) => el.addEventListener("click", () => { costState.module = el.dataset.costModule; costState.prompt = null; rerenderView(); }));
    document.querySelectorAll("[data-cost-prompt]").forEach((el) => el.addEventListener("click", () => { costState.prompt = el.dataset.costPrompt; rerenderView(); }));
    document.querySelectorAll("[data-cost-level]").forEach((el) => el.addEventListener("click", () => { costState.module = null; costState.prompt = null; rerenderView(); }));
    // invocation drill from cost / prompt-detail
    document.querySelectorAll("[data-invocation]").forEach((el) => el.addEventListener("click", () => setView("invocation")));
    document.querySelectorAll("[data-cost-drill]").forEach((el) => el.addEventListener("click", () => { costState.module = "rag"; costState.prompt = "rag.qa"; setView("cost"); }));

    // playground
    document.querySelectorAll("[data-pg-model]").forEach((el) => el.addEventListener("click", (e) => {
      e.preventDefault();
      const id = el.dataset.pgModel;
      if (pgState.models.includes(id)) pgState.models = pgState.models.filter((x) => x !== id);
      else pgState.models.push(id);
      rerenderView();
    }));
    const runBtn = document.querySelector("[data-pg-run]");
    if (runBtn) runBtn.addEventListener("click", () => { pgState.ran = true; pgState.focus = pgState.models[0]; rerenderView(); });
    document.querySelectorAll("[data-pg-focus]").forEach((el) => el.addEventListener("click", () => { pgState.focus = pgState.focus === el.dataset.pgFocus ? null : el.dataset.pgFocus; rerenderView(); }));
  }

  // Re-render just the view container (keeps nav state, rebinds handlers).
  function rerenderView() {
    document.getElementById("view").innerHTML = VIEWS[current]();
    bind();
  }

  // ⌘K is decorative here; just nudge the search button.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && current === "playground") { e.preventDefault(); pgState.ran = true; pgState.focus = pgState.models[0]; rerenderView(); }
  });

  document.addEventListener("DOMContentLoaded", render);
})();
