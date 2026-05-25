import type {
  SuiteSummary,
  EvalRun,
  RunDetailResponse,
  RunDiff,
  PromptSummary,
  PromptVersion,
  DiffResponse,
  ModelVersion,
  ModelCompareReport,
  CostResponse,
  PlaygroundAssertionDef,
  PlaygroundEvalResponse,
  PromptContent,
  PromptStats,
  PromptRun,
  CostCoverage,
  LintFinding,
  InvocationRow,
  InvocationDetail,
  BisectResult,
  BudgetStatus,
} from "./types";

function getBaseUrl(): string {
  // The dashboard HTML is always served by the same FastAPI process that
  // serves /api/*, so a relative URL points at the right backend no matter
  // how the page was reached (localhost, a VM's public IP, an ssh tunnel,
  // a reverse-proxied subdomain, etc). The previous logic hard-coded
  // http://localhost:8420 for non-localhost hostnames, which broke every
  // remote dashboard: the browser would fetch from the *user's* machine
  // instead of the server hosting the dashboard.
  return "";
}

const BASE = getBaseUrl();

/** The backend host the dashboard talks to. Same-origin today (BASE=""),
 *  but if BASE is ever a remote URL (hosted-frontend model), shows that. */
export function backendHost(): string {
  if (BASE) {
    try { return new URL(BASE, window.location.origin).host; } catch { return BASE; }
  }
  return window.location.host;
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// ---- Suites ----

export function getSuites(): Promise<SuiteSummary[]> {
  return fetchJson("/api/suites");
}

export function getSuiteRuns(
  name: string,
  limit = 20
): Promise<EvalRun[]> {
  return fetchJson(`/api/suite/${encodeURIComponent(name)}/runs?limit=${limit}`);
}

export function getRunDetail(
  name: string,
  runId: number
): Promise<RunDetailResponse> {
  return fetchJson(
    `/api/suite/${encodeURIComponent(name)}/run/${runId}`
  );
}

export function getRunDiff(
  currentId: number,
  baselineId: number
): Promise<RunDiff> {
  return fetchJson(`/api/runs/${currentId}/diff/${baselineId}`);
}

// ---- Prompts ----

export function getPrompts(): Promise<PromptSummary[]> {
  return fetchJson("/api/prompts");
}

export function getPromptVersions(
  name: string
): Promise<{ versions: PromptVersion[] }> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}`);
}

export function getPromptDiff(
  name: string,
  v1: number,
  v2: number
): Promise<DiffResponse> {
  return fetchJson(
    `/api/prompts/${encodeURIComponent(name)}/diff?v1=${v1}&v2=${v2}`
  );
}

export function getPromptContent(
  name: string,
  version?: number
): Promise<PromptContent> {
  const q = version != null ? `?v=${version}` : "";
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/content${q}`);
}

export function getPromptStats(name: string, days = 30): Promise<PromptStats> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/stats?days=${days}`);
}

export function getPromptRuns(name: string): Promise<{ runs: PromptRun[] }> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/runs`);
}

export function getCostCoverage(days = 30): Promise<CostCoverage> {
  return fetchJson(`/api/cost/coverage?days=${days}`);
}

export function listBudgets(): Promise<{ budgets: BudgetStatus[] }> {
  return fetchJson(`/api/budgets`);
}
export async function createBudget(b: { scope: string; target?: string | null; period: string; limit_usd: number }) {
  const res = await fetch(`${BASE}/api/budgets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
export async function deleteBudget(id: number) {
  const res = await fetch(`${BASE}/api/budgets/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export function listInvocations(params: { name?: string; days?: number; limit?: number; capturedOnly?: boolean; order?: "recent" | "cost"; minRating?: number } = {}): Promise<{ invocations: InvocationRow[] }> {
  const q = new URLSearchParams();
  if (params.name) q.set("name", params.name);
  q.set("days", String(params.days ?? 7));
  q.set("limit", String(params.limit ?? 100));
  if (params.capturedOnly) q.set("captured_only", "true");
  if (params.order) q.set("order", params.order);
  if (params.minRating != null) q.set("min_rating", String(params.minRating));
  return fetchJson(`/api/invocations?${q.toString()}`);
}

export function getInvocation(id: number): Promise<InvocationDetail> {
  return fetchJson(`/api/invocations/${id}`);
}

export async function promotePrompt(name: string, version: number, env: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/api/prompts/${encodeURIComponent(name)}/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version, env }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json();
}

export function getSuiteBisect(name: string): Promise<BisectResult> {
  return fetchJson(`/api/suite/${encodeURIComponent(name)}/bisect`);
}

export async function lintPromptText(content: string): Promise<{ variables: string[]; lint: LintFinding[] }> {
  const res = await fetch(`${BASE}/api/prompts/lint`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function savePromptContent(
  name: string,
  content: string
): Promise<{ ok: boolean; version: number; hash: string }> {
  const res = await fetch(
    `${BASE}/api/prompts/${encodeURIComponent(name)}/content`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json();
}

// ---- Models ----

export function getModelVersions(
  suite: string
): Promise<{ versions: ModelVersion[] }> {
  return fetchJson(`/api/models/${encodeURIComponent(suite)}`);
}

export function compareModels(
  suite: string,
  baseline: string,
  candidate: string
): Promise<ModelCompareReport> {
  return fetchJson(
    `/api/models/${encodeURIComponent(suite)}/compare?baseline=${encodeURIComponent(baseline)}&candidate=${encodeURIComponent(candidate)}`
  );
}

// ---- Cost ----

// ---- Playground ----

export async function runPlaygroundEval(
  response: string,
  assertions: PlaygroundAssertionDef[],
): Promise<PlaygroundEvalResponse> {
  const res = await fetch(`${BASE}/api/playground/eval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response, assertions }),
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

export interface PlaygroundModelRunResponse {
  response: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
}

export async function runPlaygroundModel(req: {
  model: string;
  system: string;
  user: string;
  context: string;
  temperature: number;
}): Promise<PlaygroundModelRunResponse> {
  const res = await fetch(`${BASE}/api/playground/model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// ---- Cost ----

export function getCostData(
  days = 7,
  name?: string,
  model?: string
): Promise<CostResponse> {
  const params = new URLSearchParams({ days: String(days) });
  if (name) params.set("name", name);
  if (model) params.set("model", model);
  return fetchJson(`/api/cost?${params.toString()}`);
}
