/** All public interfaces for promptry. */

export interface PromptryOptions {
  /** Remote ingest endpoint URL (your self-hosted promptry server). */
  endpoint: string;
  /** Optional API key (sent as Bearer token). */
  apiKey?: string;
  /** Optional project identifier, added to event metadata. */
  projectId?: string;
  /** Number of events before auto-flush. Default 10. */
  batchSize?: number;
  /** Milliseconds between timer flushes. Default 5000. */
  flushInterval?: number;
  /** Fraction of calls that actually ship (0–1). Default 1.0. */
  sampleRate?: number;
}

export interface TrackOptions {
  metadata?: Record<string, unknown>;
}

/** Arguments for trackInvocation() — one LLM call's cost/latency/tokens. */
export interface InvocationOptions {
  /** Prompt/module name this call belongs to. */
  name: string;
  /** Model identifier, e.g. "claude-opus-4-8". */
  model?: string;
  /** Prompt input tokens. Default 0. */
  tokensIn?: number;
  /** Completion output tokens. Default 0. */
  tokensOut?: number;
  /** Dollar cost of the call, if you compute it client-side. */
  cost?: number;
  /** End-to-end latency in milliseconds. */
  latencyMs?: number;
  /** Correlation id so trackFeedback() can tie a rating back to this call. */
  requestId?: string;
  /** Provider response id — dedups the same call seen by two capture layers. */
  responseId?: string;
  /** Cached (prefix-cache) input tokens, if the provider reports them. */
  cachedTokens?: number;
  /** Arbitrary extra metadata. */
  metadata?: Record<string, unknown>;
}

/** Arguments for trackFeedback() — an end-user rating/comment. */
export interface FeedbackOptions {
  /** The requestId of the invocation this feedback is about. */
  requestId: string;
  /** Numeric rating (e.g. 1 / -1, or 1–5). */
  rating?: number;
  /** Free-text comment. */
  comment?: string;
  /** Where the feedback came from (e.g. "thumbs", "survey"). */
  source?: string;
  /** Arbitrary extra metadata. */
  metadata?: Record<string, unknown>;
}

// ---- wire event shapes (must match docs/wire-schema/events.schema.json) ----

export interface PromptSaveData {
  name: string;
  version: null;
  content: string;
  hash: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface InvocationData {
  name: string;
  model?: string;
  tokens_in: number;
  tokens_out: number;
  cost?: number;
  latency_ms?: number;
  request_id?: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface FeedbackData {
  request_id: string;
  rating?: number;
  comment?: string;
  source?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export type TelemetryEvent =
  | { type: 'prompt_save'; data: PromptSaveData; timestamp: string }
  | { type: 'invocation'; data: InvocationData; timestamp: string }
  | { type: 'feedback'; data: FeedbackData; timestamp: string };

export interface EventBatch {
  events: TelemetryEvent[];
}

export interface OfflineStore {
  load(): TelemetryEvent[];
  save(events: TelemetryEvent[]): void;
  clear(): void;
}
