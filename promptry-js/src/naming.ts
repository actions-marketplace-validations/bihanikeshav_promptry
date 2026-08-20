/**
 * Zero-config prompt naming + cost-attributed call trees (mirrors the Python
 * promptry.naming / promptry.trace). Names are inferred from the call site so
 * you don't have to invent one; call trees group the LLM calls made inside a
 * trace() block via AsyncLocalStorage.
 */
import { AsyncLocalStorage } from 'node:async_hooks';
import { randomBytes } from 'node:crypto';

const _taskStore = new AsyncLocalStorage<string>();
const _traceStore = new AsyncLocalStorage<{ traceId: string; span: string }>();

/** Name every tracked call inside `fn` explicitly (the escape hatch for a
 * generic call_llm() helper where the call site is meaningless). */
export function task<T>(name: string, fn: () => T): T {
  return _taskStore.run(name, fn);
}

/** Group the LLM calls made inside `fn` into one cost-attributed call tree.
 * Nested traces share the outer trace id; the innermost name labels the step. */
export function trace<T>(name: string, fn: (traceId: string) => T): T {
  const parent = _traceStore.getStore();
  const traceId = parent?.traceId ?? randomBytes(8).toString('hex');
  return _traceStore.run({ traceId, span: name }, () => fn(traceId));
}

export function currentTrace(): { traceId: string; span: string } | undefined {
  return _traceStore.getStore();
}

const _DENY = [
  'promptry',
  'node:internal',
  'node_modules/openai',
  'node_modules/@anthropic',
  'node_modules/undici',
];

function _inferFromStack(): string {
  const stack = new Error().stack;
  if (!stack) return 'unknown';
  const lines = stack.split('\n').slice(1);
  for (const raw of lines) {
    const line = raw.trim();
    // "at fnName (file:line:col)" | "at file:line:col"
    const m = line.match(/^at\s+(?:(.+?)\s+\()?(.+?):(\d+):(\d+)\)?$/);
    if (!m) continue;
    const fn = m[1];
    const file = m[2] || '';
    if (_DENY.some((d) => file.includes(d))) continue;
    if (file.startsWith('node:')) continue;
    const base =
      (file.split(/[\\/]/).pop() || file).replace(/\.[jt]sx?$/, '') || 'app';
    const name = fn && fn !== '<anonymous>' ? fn.split(' ').pop() : undefined;
    return name ? `${base}:${name}` : base;
  }
  return 'unknown';
}

/** Resolve a name: explicit > ambient task() > call site > "unknown". */
export function inferTask(explicit?: string): string {
  if (explicit) return explicit;
  const ambient = _taskStore.getStore();
  if (ambient) return ambient;
  try {
    return _inferFromStack();
  } catch {
    return 'unknown';
  }
}
