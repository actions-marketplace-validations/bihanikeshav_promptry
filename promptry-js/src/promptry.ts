/** Main Promptry class: track(), trackContext(), trackInvocation(),
 * trackFeedback(), flush(), destroy(). */

import { sha256 } from './hash';
import { DedupCache } from './dedup';
import { Batcher } from './batcher';
import type {
  FeedbackOptions,
  InvocationOptions,
  PromptryOptions,
  TelemetryEvent,
  TrackOptions,
} from './types';

export class Promptry {
  private _batcher: Batcher;
  private _dedup = new DedupCache();
  private _sampleRate: number;
  private _projectId?: string;
  /** Synchronous pre-check set using "name:contentSlice" */
  private _quickCheck = new Set<string>();
  private static readonly QUICK_CHECK_MAX = 20_000;
  /** In-flight async operations (hash + enqueue). */
  private _pending: Promise<void>[] = [];

  constructor(opts: PromptryOptions) {
    this._sampleRate = opts.sampleRate ?? 1.0;
    this._projectId = opts.projectId;

    this._batcher = new Batcher({
      transport: {
        endpoint: opts.endpoint,
        apiKey: opts.apiKey,
      },
      batchSize: opts.batchSize ?? 10,
      flushInterval: opts.flushInterval ?? 5000,
    });
  }

  /**
   * Track a prompt. Returns `content` unchanged (synchronous).
   * Hash + enqueue happens as a fire-and-forget async operation.
   */
  track(content: string, name: string, opts?: TrackOptions): string {
    // Sampling
    if (this._sampleRate < 1.0 && Math.random() > this._sampleRate) {
      return content;
    }

    // Fast synchronous pre-check
    const quickKey = `${name}:${content.slice(0, 128)}`;
    if (this._quickCheck.has(quickKey)) {
      return content;
    }

    // Add to quick-check (bounded)
    if (this._quickCheck.size >= Promptry.QUICK_CHECK_MAX) {
      this._quickCheck.clear();
    }
    this._quickCheck.add(quickKey);

    // Fire-and-forget async hash + enqueue
    this._enqueueOp(this._trackAsync(content, name, opts?.metadata));

    return content;
  }

  /**
   * Track retrieval context chunks. Returns `chunks` unchanged (synchronous).
   * Internally joins with "\n---\n", name gets ":context" suffix.
   */
  trackContext(
    chunks: string[],
    name: string,
    opts?: TrackOptions,
  ): string[] {
    if (this._sampleRate < 1.0 && Math.random() > this._sampleRate) {
      return chunks;
    }

    const joined = chunks.join('\n---\n');
    const contextName = `${name}:context`;
    const meta: Record<string, unknown> = { ...opts?.metadata };
    meta['chunk_count'] = chunks.length;

    // Fast synchronous pre-check
    const quickKey = `${contextName}:${joined.slice(0, 128)}`;
    if (this._quickCheck.has(quickKey)) {
      return chunks;
    }

    if (this._quickCheck.size >= Promptry.QUICK_CHECK_MAX) {
      this._quickCheck.clear();
    }
    this._quickCheck.add(quickKey);

    this._enqueueOp(this._trackAsync(joined, contextName, meta));

    return chunks;
  }

  /**
   * Track a prompt. Alias of track() with a clearer name alongside
   * trackInvocation()/trackFeedback(). Returns `content` unchanged.
   */
  trackPrompt(content: string, name: string, opts?: TrackOptions): string {
    return this.track(content, name, opts);
  }

  /**
   * Track one LLM invocation (cost / latency / tokens). Discrete event,
   * never deduped — every call is its own row, mirroring the Python
   * invocations ledger. Returns nothing; ships in the background.
   */
  trackInvocation(opts: InvocationOptions): void {
    if (this._sampleRate < 1.0 && Math.random() > this._sampleRate) {
      return;
    }
    const now = new Date().toISOString();
    const event: TelemetryEvent = {
      type: 'invocation',
      data: {
        name: opts.name,
        ...(opts.model !== undefined ? { model: opts.model } : {}),
        tokens_in: opts.tokensIn ?? 0,
        tokens_out: opts.tokensOut ?? 0,
        ...(opts.cost !== undefined ? { cost: opts.cost } : {}),
        ...(opts.latencyMs !== undefined ? { latency_ms: opts.latencyMs } : {}),
        ...(opts.requestId !== undefined
          ? { request_id: opts.requestId }
          : {}),
        metadata: this._withProject(opts.metadata),
        created_at: now,
      },
      timestamp: now,
    };
    this._batcher.enqueue(event);
  }

  /**
   * Track end-user feedback (rating / comment) for a prior invocation,
   * correlated by requestId. Always ships (feedback is rare and valuable,
   * so it is not sampled). Returns nothing; ships in the background.
   */
  trackFeedback(opts: FeedbackOptions): void {
    const now = new Date().toISOString();
    const meta = this._withProject(opts.metadata);
    const event: TelemetryEvent = {
      type: 'feedback',
      data: {
        request_id: opts.requestId,
        ...(opts.rating !== undefined ? { rating: opts.rating } : {}),
        ...(opts.comment !== undefined ? { comment: opts.comment } : {}),
        ...(opts.source !== undefined ? { source: opts.source } : {}),
        ...(Object.keys(meta).length > 0 ? { metadata: meta } : {}),
        created_at: now,
      },
      timestamp: now,
    };
    this._batcher.enqueue(event);
  }

  /** Merge caller metadata with the configured projectId. */
  private _withProject(
    metadata?: Record<string, unknown>,
  ): Record<string, unknown> {
    const meta: Record<string, unknown> = { ...metadata };
    if (this._projectId) {
      meta['project_id'] = this._projectId;
    }
    return meta;
  }

  /** Flush all pending events (waits for in-flight hash ops first). */
  async flush(): Promise<void> {
    await Promise.all(this._pending);
    await this._batcher.flush();
  }

  /** Flush + teardown timers. */
  async destroy(): Promise<void> {
    await this._batcher.destroy();
  }

  private _enqueueOp(op: Promise<void>): void {
    this._pending.push(op);
    op.finally(() => {
      const idx = this._pending.indexOf(op);
      if (idx >= 0) this._pending.splice(idx, 1);
    });
  }

  private async _trackAsync(
    content: string,
    name: string,
    metadata?: Record<string, unknown>,
  ): Promise<void> {
    try {
      const hash = await sha256(content);
      const cacheKey = `${name}:${hash}`;

      if (this._dedup.has(cacheKey)) return;
      this._dedup.add(cacheKey);

      const now = new Date().toISOString();
      const meta: Record<string, unknown> = { ...metadata };
      if (this._projectId) {
        meta['project_id'] = this._projectId;
      }

      const event: TelemetryEvent = {
        type: 'prompt_save',
        data: {
          name,
          version: null,
          content,
          hash,
          metadata: meta,
          created_at: now,
        },
        timestamp: now,
      };

      this._batcher.enqueue(event);
    } catch {
      // Telemetry should never break the caller
    }
  }
}
