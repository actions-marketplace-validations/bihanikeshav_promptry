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
  FeedbackRow,
  FeedbackStats,
  InvocationDetail,
  BisectResult,
  BudgetStatus,
  ProjectConfig,
  OnlineDrift,
  PiiScan,
  NearDuplicates,
  Diff2Response,
  CacheAnalysisList,
  PromptCacheAnalysis,
  ShortenAnalysisList,
  PromptShortenAnalysis,
  SuiteCandidatesResponse,
  CreateSuiteRequest,
  CreateSuiteResponse,
  SuiteDefinitionResponse,
  RecordedContextResponse,
  GoldenExample,
  GoldenRunResult,
  OnboardingStatus,
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

/** Origin of the API (no trailing slash). Prefer page origin over hard-coded ports. */
export function backendOrigin(): string {
  if (BASE) {
    try { return new URL(BASE, window.location.origin).origin; } catch { /* fall through */ }
  }
  return window.location.origin;
}

/** Example curl for feedback ingest — host matches how the user opened the UI. */
export function feedbackCurlExample(opts: { authRequired?: boolean } = {}): string {
  const origin = backendOrigin();
  const auth = opts.authRequired
    ? ` -H 'Authorization: Bearer $PROMPTRY_AUTH_TOKEN'`
    : "";
  return (
    `curl -X POST ${origin}/api/feedback` +
    ` -H 'Content-Type: application/json'` +
    auth +
    ` -d '{"request_id":"abc","rating":1}'`
  );
}

export class AuthError extends Error {
  constructor(message = "authentication required") {
    super(message);
    this.name = "AuthError";
  }
}

export type AuthStatus = {
  required: boolean;
  authenticated: boolean;
  posture?: "open" | "token" | "multiuser";
  role?: string | null;
  email?: string | null;
};

/** Fired when any API call gets 401 so the auth gate can show the login form. */
export const AUTH_REQUIRED_EVENT = "promptry:auth-required";

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
  });
  if (res.status === 401) {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }
    throw new AuthError();
  }
  return res;
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await request(path);
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

async function fetchJsonMutate<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await request(path, { ...init, headers });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  // 204 / empty body
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// ---- Auth ----

export function getAuthStatus(): Promise<AuthStatus> {
  // status is public even when auth is on — do not go through request() 401 path
  // for the initial gate check (unauthenticated users must read this).
  return fetch(`${BASE}/api/auth/status`, { credentials: "include" }).then(async (res) => {
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json();
  });
}

export async function login(token: string): Promise<AuthStatus & { ok: boolean }> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || `login failed (${res.status})`);
  }
  return data as AuthStatus & { ok: boolean };
}

export async function logout(): Promise<AuthStatus & { ok: boolean }> {
  return fetchJsonMutate("/api/auth/logout", { method: "POST" });
}

/** Multi-user login with email + password (posture === "multiuser"). */
export async function loginUser(
  email: string,
  password: string
): Promise<{ ok: boolean; authenticated: boolean; role?: string; email?: string }> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || `login failed (${res.status})`);
  }
  return data as { ok: boolean; authenticated: boolean; role?: string; email?: string };
}

export type Me = {
  kind: string;
  email: string | null;
  role: string;
  user_id: number | null;
};

/** The current caller's identity + role (for nav/route gating). */
export function getMe(): Promise<Me> {
  return fetch(`${BASE}/api/auth/me`, { credentials: "include" }).then(async (res) => {
    if (!res.ok) throw new Error(`API error ${res.status}`);
    return res.json();
  });
}

// ---- Users (admin) ----

export type User = {
  id: number;
  email: string;
  name: string | null;
  role: string;
  is_active: number;
  created_at: string;
  last_login_at: string | null;
};

export function listUsers(): Promise<{ users: User[] }> {
  return fetchJson("/api/users");
}

export function createUser(body: {
  email: string;
  password?: string;
  name?: string;
  role: string;
}): Promise<{ user: User; bootstrap: boolean }> {
  return fetchJsonMutate("/api/users", { method: "POST", body: JSON.stringify(body) });
}

export function updateUser(
  id: number,
  body: { role?: string; is_active?: boolean; name?: string }
): Promise<{ user: User }> {
  return fetchJsonMutate(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteUser(id: number): Promise<{ ok: boolean }> {
  return fetchJsonMutate(`/api/users/${id}`, { method: "DELETE" });
}

export function setUserPassword(id: number, password: string): Promise<{ ok: boolean }> {
  return fetchJsonMutate(`/api/users/${id}/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

// ---- Audit log (admin) ----

export type AuditEntry = {
  id: number;
  ts: string;
  actor: string | null;
  actor_id: number | null;
  action: string;
  target: string | null;
  ip: string | null;
  result: string;
  detail: Record<string, unknown> | null;
};

export function listAudit(
  params: { limit?: number; offset?: number; action?: string } = {}
): Promise<{ entries: AuditEntry[]; total: number; limit: number; offset: number }> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.offset) q.set("offset", String(params.offset));
  if (params.action) q.set("action", params.action);
  const qs = q.toString();
  return fetchJson(`/api/audit${qs ? "?" + qs : ""}`);
}

// ---- Onboarding ----

export function getOnboardingStatus(): Promise<OnboardingStatus> {
  return fetchJson("/api/onboarding-status");
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

export function getOnlineDrift(name: string, days = 30): Promise<OnlineDrift> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/online-drift?days=${days}`);
}

export function getNearDuplicates(threshold = 0.85): Promise<NearDuplicates> {
  return fetchJson(`/api/prompts/near-duplicates?threshold=${threshold}`);
}

/** Side-by-side content diff of two prompts + shared-prefix / cache analysis. */
export function getPromptDiff2(a: string, b: string): Promise<Diff2Response> {
  return fetchJson(
    `/api/prompts/diff2?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`
  );
}

/** All prompts ranked by prefix-cache reorder opportunity. */
export function getCacheAnalysisList(): Promise<CacheAnalysisList> {
  return fetchJson(`/api/prompts/cache-analysis`);
}

/** Full per-prompt input-placement cache analysis (segment breakdown). */
export function getPromptCacheAnalysis(name: string): Promise<PromptCacheAnalysis> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/cache-analysis`);
}

/** All prompts ranked by estimated shorten savings. */
export function getShortenAnalysisList(): Promise<ShortenAnalysisList> {
  return fetchJson(`/api/prompts/shorten-analysis`);
}

/** Per-prompt shorten analysis: redundant/filler findings + measured savings. */
export function getPromptShortenAnalysis(name: string): Promise<PromptShortenAnalysis> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/shorten-analysis`);
}

// ---- Suite creator ----

export function getSuiteCandidates(params: {
  source: "golden" | "feedback";
  name?: string;
  minRating?: number;
  limit?: number;
}): Promise<SuiteCandidatesResponse> {
  const q = new URLSearchParams({ source: params.source });
  if (params.name) q.set("name", params.name);
  if (params.minRating != null) q.set("min_rating", String(params.minRating));
  if (params.limit != null) q.set("limit", String(params.limit));
  return fetchJson(`/api/suite-candidates?${q.toString()}`);
}

export async function createSuite(body: CreateSuiteRequest): Promise<CreateSuiteResponse> {
  return fetchJsonMutate("/api/suites", { method: "POST", body: JSON.stringify(body) });
}

export function getSuiteDefinition(name: string): Promise<SuiteDefinitionResponse> {
  return fetchJson(`/api/suites/${encodeURIComponent(name)}/definition`);
}

export function getRecordedContext(name: string): Promise<RecordedContextResponse> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/recorded-context`);
}

// ---- Eval-from-trace: per-prompt golden set ----

export function listExamples(name: string): Promise<{ examples: GoldenExample[] }> {
  return fetchJson(`/api/prompts/${encodeURIComponent(name)}/examples`);
}
export async function addExample(name: string, invocationId: number): Promise<{ ok: boolean; id: number }> {
  return fetchJsonMutate(`/api/prompts/${encodeURIComponent(name)}/examples`, {
    method: "POST",
    body: JSON.stringify({ invocation_id: invocationId }),
  });
}
export async function deleteExample(id: number): Promise<{ ok: boolean }> {
  return fetchJsonMutate(`/api/examples/${id}`, { method: "DELETE" });
}
export async function runExamples(name: string, model: string, threshold = 0.8): Promise<GoldenRunResult> {
  return fetchJsonMutate(`/api/prompts/${encodeURIComponent(name)}/examples/run`, {
    method: "POST",
    body: JSON.stringify({ model, threshold }),
  });
}

export function getCostCoverage(days = 30): Promise<CostCoverage> {
  return fetchJson(`/api/cost/coverage?days=${days}`);
}

export function getConfig(): Promise<ProjectConfig> {
  return fetchJson(`/api/config`);
}
export async function updateConfig(body: Partial<ProjectConfig>) {
  return fetchJsonMutate(`/api/config`, { method: "POST", body: JSON.stringify(body) });
}

export function listBudgets(): Promise<{ budgets: BudgetStatus[] }> {
  return fetchJson(`/api/budgets`);
}
export async function createBudget(b: { scope: string; target?: string | null; period: string; limit_usd: number }) {
  return fetchJsonMutate(`/api/budgets`, { method: "POST", body: JSON.stringify(b) });
}
export async function deleteBudget(id: number) {
  return fetchJsonMutate(`/api/budgets/${id}`, { method: "DELETE" });
}

export function listInvocations(params: { name?: string; days?: number; limit?: number; offset?: number; capturedOnly?: boolean; order?: "recent" | "cost"; sort?: string; direction?: "asc" | "desc"; minRating?: number } = {}): Promise<{ invocations: InvocationRow[] }> {
  const q = new URLSearchParams();
  if (params.name) q.set("name", params.name);
  q.set("days", String(params.days ?? 7));
  q.set("limit", String(params.limit ?? 100));
  if (params.offset) q.set("offset", String(params.offset));
  if (params.capturedOnly) q.set("captured_only", "true");
  if (params.order) q.set("order", params.order);
  if (params.sort) q.set("sort", params.sort);
  if (params.direction) q.set("direction", params.direction);
  if (params.minRating != null) q.set("min_rating", String(params.minRating));
  return fetchJson(`/api/invocations?${q.toString()}`);
}

export function listFeedback(params: { name?: string; days?: number; limit?: number; offset?: number; onlyComments?: boolean; minRating?: number; q?: string } = {}): Promise<{ feedback: FeedbackRow[] }> {
  const p = new URLSearchParams();
  if (params.name) p.set("name", params.name);
  p.set("days", String(params.days ?? 30));
  p.set("limit", String(params.limit ?? 50));
  if (params.offset) p.set("offset", String(params.offset));
  if (params.onlyComments) p.set("only_comments", "true");
  if (params.minRating != null) p.set("min_rating", String(params.minRating));
  if (params.q) p.set("q", params.q);
  return fetchJson(`/api/feedback?${p.toString()}`);
}

export function getFeedbackStats(days = 30): Promise<FeedbackStats> {
  return fetchJson(`/api/feedback/stats?days=${days}`);
}

export function getInvocation(id: number): Promise<InvocationDetail> {
  return fetchJson(`/api/invocations/${id}`);
}

export function getInvocationScan(id: number): Promise<PiiScan> {
  return fetchJson(`/api/invocations/${id}/scan`);
}

export async function promotePrompt(name: string, version: number, env: string): Promise<{ ok: boolean }> {
  return fetchJsonMutate(`/api/prompts/${encodeURIComponent(name)}/promote`, {
    method: "POST",
    body: JSON.stringify({ version, env }),
  });
}

export function getSuiteBisect(name: string): Promise<BisectResult> {
  return fetchJson(`/api/suite/${encodeURIComponent(name)}/bisect`);
}

export async function lintPromptText(content: string): Promise<{ variables: string[]; lint: LintFinding[] }> {
  return fetchJsonMutate(`/api/prompts/lint`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function savePromptContent(
  name: string,
  content: string
): Promise<{ ok: boolean; version: number; hash: string }> {
  return fetchJsonMutate(`/api/prompts/${encodeURIComponent(name)}/content`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
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
  return fetchJsonMutate(`/api/playground/eval`, {
    method: "POST",
    body: JSON.stringify({ response, assertions }),
  });
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
  return fetchJsonMutate(`/api/playground/model`, {
    method: "POST",
    body: JSON.stringify(req),
  });
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
