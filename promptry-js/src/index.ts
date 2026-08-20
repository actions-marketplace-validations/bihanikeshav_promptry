/** Re-exports + convenience singleton (mirrors Python module-level track()). */

export { Promptry } from './promptry';
// Zero-config naming + cost-attributed call trees (mirrors promptry.trace/task).
export { task, trace, currentTrace, inferTask } from './naming';
export type {
  PromptryOptions,
  TrackOptions,
  InvocationOptions,
  FeedbackOptions,
  TelemetryEvent,
  EventBatch,
  OfflineStore,
} from './types';

import { Promptry } from './promptry';
import type {
  FeedbackOptions,
  InvocationOptions,
  PromptryOptions,
  TrackOptions,
} from './types';

let _instance: Promptry | null = null;

/** Initialize the global singleton. */
export function init(opts: PromptryOptions): Promptry {
  if (_instance) {
    void _instance.destroy();
  }
  _instance = new Promptry(opts);
  return _instance;
}

function getInstance(): Promptry {
  if (!_instance) {
    throw new Error(
      'promptry-js not initialized. Call init({ endpoint: "..." }) first.',
    );
  }
  return _instance;
}

/** Track a prompt via the global singleton. Returns content unchanged. */
export function track(
  content: string,
  name: string,
  opts?: TrackOptions,
): string {
  return getInstance().track(content, name, opts);
}

/** Track a prompt via the global singleton (alias of track). */
export function trackPrompt(
  content: string,
  name: string,
  opts?: TrackOptions,
): string {
  return getInstance().trackPrompt(content, name, opts);
}

/** Track context chunks via the global singleton. Returns chunks unchanged. */
export function trackContext(
  chunks: string[],
  name: string,
  opts?: TrackOptions,
): string[] {
  return getInstance().trackContext(chunks, name, opts);
}

/** Track one LLM invocation (cost/latency/tokens) via the global singleton. */
export function trackInvocation(opts: InvocationOptions): void {
  getInstance().trackInvocation(opts);
}

/** Track end-user feedback via the global singleton. */
export function trackFeedback(opts: FeedbackOptions): void {
  getInstance().trackFeedback(opts);
}

/** Flush all pending events from the global singleton. */
export function flush(): Promise<void> {
  return getInstance().flush();
}

/** Flush + teardown the global singleton. */
export async function destroy(): Promise<void> {
  if (_instance) {
    await _instance.destroy();
    _instance = null;
  }
}
