export interface SuiteSummary {
  name: string;
  latest_score: number | null;
  passed: boolean;
  model_version: string | null;
  prompt_version: number | null;
  timestamp: string;
  drift_status: "stable" | "drifting";
  drift_slope: number;
  sparkline_scores: number[];
}

export interface EvalRun {
  id: number;
  suite_name: string;
  prompt_name: string | null;
  prompt_version: number | null;
  model_version: string | null;
  timestamp: string;
  overall_pass: boolean;
  overall_score: number | null;
}

export interface AssertionResult {
  id: number;
  run_id: number;
  test_name: string;
  assertion_type: string;
  passed: boolean;
  score: number | null;
  details: Record<string, unknown> | null;
  latency_ms: number | null;
}

export interface JudgeCost {
  calls: number;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  estimated: boolean;
  unpriced: boolean;
}

export interface RunDetailResponse {
  run: EvalRun;
  assertions: AssertionResult[];
  judge?: JudgeCost | null;
}

export interface PromptSummary {
  name: string;
  latest_version: number;
  tags: string[];
}

export interface PromptVersion {
  version: number;
  hash: string;
  created_at: string;
  tags: string[];
}

export interface PromptContent {
  name: string;
  version: number;
  content: string;
  hash: string;
  created_at: string;
  tags: string[];
  variables?: string[];
  lint?: { level: "error" | "warning" | "info"; message: string }[];
}

export interface PromptRun {
  run_id: number;
  suite_name: string;
  prompt_version: number | null;
  model_version: string | null;
  timestamp: string;
  passed: boolean;
  score: number | null;
}

export interface LintFinding {
  level: "error" | "warning" | "info";
  message: string;
}

export interface InvocationRow {
  id: number;
  prompt_name: string;
  prompt_version: number | null;
  created_at: string;
  model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost: number | null;
  latency_ms: number | null;
  has_capture: boolean;
  output_preview: string;
  rating: number | null;
  comment: string | null;
}

export interface FeedbackEntry {
  rating: number | null;
  comment: string | null;
  source: string | null;
  created_at: string;
}

export interface InvocationBreakdown {
  available?: boolean;
  reason?: string;
  model?: string | null;
  template_tokens?: number;
  data_tokens?: number;
  tokens_out?: number;
  template_cost?: number | null;
  data_cost?: number | null;
  output_cost?: number | null;
  total_cost?: number | null;
  estimated?: boolean;
}

export interface InvocationDetail {
  id: number;
  prompt_name: string;
  prompt_version: number | null;
  created_at: string;
  request_id: string | null;
  metadata: Record<string, unknown>;
  input_text: string | null;
  output_text: string | null;
  feedback: FeedbackEntry[];
  breakdown: InvocationBreakdown | null;
}

export interface BisectResult {
  found: boolean;
  suite: string;
  reason?: string;
  first_bad?: { run_id: number; prompt_version: number | null; model_version: string | null; timestamp: string; score: number | null };
  last_good?: { run_id: number; prompt_version: number | null; model_version: string | null; timestamp: string; score: number | null };
  prompt_changed?: boolean;
  model_changed?: boolean;
}

export interface GoldenExample {
  id: number;
  prompt_name: string;
  input_text: string;
  reference_output: string | null;
  source_invocation_id: number | null;
  model: string | null;
  created_at: string;
}

export interface GoldenRunItem {
  id: number;
  score: number;
  passed: boolean;
  output_preview: string;
  reference_preview: string;
  latency_ms: number;
  error: string | null;
}

export interface GoldenRunResult {
  prompt_name: string;
  model: string;
  threshold: number;
  mode: "semantic" | "lexical" | "none";
  count: number;
  passed: number;
  accuracy: number;
  results: GoldenRunItem[];
}

export interface NearDuplicatePair {
  a: string;
  b: string;
  similarity: number;
}

export interface NearDuplicates {
  mode: "semantic" | "lexical" | "none";
  threshold: number;
  pairs: NearDuplicatePair[];
}

export interface PiiFinding {
  type: string;
  category: "secret" | "pii";
  severity: "high" | "medium" | "low";
  count: number;
  sample: string;
}

export interface PiiScan {
  input: PiiFinding[];
  output: PiiFinding[];
  total: number;
  has_secret: boolean;
  worst_severity: "high" | "medium" | "low" | null;
}

export interface OnlineDriftMetric {
  metric: string;
  label: string;
  count: number;
  baseline_mean: number;
  recent_mean: number;
  pct_change: number | null;
  slope: number;
  p_value: number | null;
  direction: "up" | "down" | "flat";
  bad_direction: "up" | "down";
  drifting: boolean;
  severity: "high" | "medium" | "none";
  message: string;
}

export interface OnlineDrift {
  name: string;
  days: number;
  total_calls: number;
  metrics: OnlineDriftMetric[];
  drifting_count: number;
  status: "drifting" | "stable" | "insufficient";
}

export interface ModelEntry {
  id: string;
  provider?: string;
  label?: string;
}

export interface ProjectConfig {
  models: ModelEntry[];
  judge: Record<string, string>;
  dashboard: Record<string, number | string | boolean>;
  pricing: Record<string, Record<string, number>>;
  key_status: Record<string, boolean>;
  path: string;
}

export interface BudgetStatus {
  id: number;
  scope: string;
  target: string | null;
  period: string;
  limit_usd: number;
  spend: number;
  pct: number;
  breached: boolean;
}

export interface CostCoverage {
  days: number;
  models_seen: number;
  uncosted: { model: string; calls: number }[];
  uncosted_calls: number;
}

export interface MetricSummary {
  min: number;
  avg: number;
  p50: number;
  p95: number;
  max: number;
  sum: number;
}

export interface PromptStats {
  name: string;
  days: number;
  count: number;
  metrics: {
    tokens_in: MetricSummary;
    tokens_out: MetricSummary;
    cost: MetricSummary;
    latency_ms: MetricSummary;
  };
  histogram: { start: number; end: number; count: number }[];
}

export interface DiffLine {
  type: "unchanged" | "added" | "deleted";
  old_num: number | null;
  new_num: number | null;
  content: string;
}

export interface DiffResponse {
  additions: number;
  deletions: number;
  lines: DiffLine[];
}

export interface ModelVersion {
  model_version: string;
  run_count: number;
}

export interface AssertionComparison {
  assertion_type: string;
  baseline_mean: number;
  baseline_std: number;
  candidate_score: number;
  delta: number;
  verdict: "better" | "worse" | "comparable";
}

export interface ModelCompareReport {
  suite_name: string;
  baseline: {
    model_version: string;
    run_count: number;
    overall_mean: number;
    overall_std: number;
  };
  candidate: {
    model_version: string;
    run_count: number;
    overall_mean: number;
    overall_std: number;
  };
  overall_delta: number;
  percentile: number;
  assertion_comparisons: AssertionComparison[];
  cost_ratio: number | null;
  score_per_dollar_baseline: number | null;
  score_per_dollar_candidate: number | null;
  verdict: "switch" | "comparable" | "keep_baseline";
  verdict_reason: string;
}

export interface PlaygroundAssertionDef {
  type: "contains" | "not_contains" | "json_valid" | "matches";
  value?: string | string[];
  options?: Record<string, unknown>;
}

// Used by the in-browser assertion runner in the Playground (client-side).
// Server-side evaluation only supports the subset defined by PlaygroundAssertionDef.
export type PlaygroundRuleType =
  | "contains"
  | "not_contains"
  | "json_valid"
  | "json_path_eq"
  | "matches"
  | "max_tokens"
  | "similarity";

export interface PlaygroundModelRunRequest {
  model: string;
  system: string;
  user: string;
  context?: string;
  temperature?: number;
}

export interface PlaygroundModelRunResponse {
  response: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
}

export interface PlaygroundAssertionResult {
  index: number;
  type: string;
  passed: boolean;
  score: number;
  details: Record<string, unknown>;
}

export interface PlaygroundEvalResponse {
  overall_passed: boolean;
  overall_score: number;
  passed_count: number;
  total_count: number;
  results: PlaygroundAssertionResult[];
}

export interface RunDiffSide {
  passed: boolean;
  score: number | null;
  details: Record<string, unknown> | null;
  latency_ms: number | null;
}

export interface RunDiffAssertion {
  type: string;
  baseline: RunDiffSide | null;
  current: RunDiffSide | null;
  score_delta: number | null;
  status_change: "none" | "regressed" | "improved" | "passed";
}

export interface RunDiffTest {
  name: string;
  status: "passed" | "failed" | "regressed" | "improved" | "unchanged";
  assertions: RunDiffAssertion[];
}

export interface RunDiffRunMeta {
  id: number;
  suite_name: string;
  score: number | null;
  overall_pass: boolean;
  model_version: string | null;
  prompt_name: string | null;
  prompt_version: number | null;
  timestamp: string;
}

export interface RunDiff {
  current: RunDiffRunMeta;
  baseline: RunDiffRunMeta;
  score_delta: number | null;
  summary: {
    regressed: number;
    improved: number;
    unchanged: number;
    total: number;
  };
  tests: RunDiffTest[];
}

export interface CostResponse {
  summary: {
    total_cost: number;
    total_calls: number;
    total_tokens_in: number;
    total_tokens_out: number;
    total_cached_tokens?: number;
    total_cache_write_tokens?: number;
    cache_hit_rate?: number;
    cache_savings?: number;
    uncached_cost?: number;
    avg_cost: number;
  };
  by_name: {
    name: string;
    calls: number;
    tokens_in: number;
    tokens_out: number;
    cached_tokens?: number;
    cache_write_tokens?: number;
    cache_hit_rate?: number;
    cache_savings?: number;
    cost: number;
    models: string[];
  }[];
  by_date: {
    date: string;
    calls: number;
    tokens_in: number;
    tokens_out: number;
    cached_tokens?: number;
    cost: number;
  }[];
}
