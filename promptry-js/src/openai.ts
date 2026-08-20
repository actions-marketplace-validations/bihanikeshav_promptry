/**
 * Drop-in tracking for the `openai` npm client (mirrors Python
 * `from promptry.openai import OpenAI`). Wrap your client once and every chat
 * call is recorded — cost/tokens/cached-tokens/response-id, plus the active
 * call tree — for non-streaming and streaming calls, and failures. Best-effort:
 * tracking never throws into your request, and the real response is unchanged.
 *
 *   import { init } from 'promptry-js';
 *   import { wrapOpenAI } from 'promptry-js/openai';
 *   init({ endpoint: '...' });
 *   const client = wrapOpenAI(new OpenAI());
 *   await client.chat.completions.create({ model: 'gpt-4o', messages: [...] });
 */
import { trackInvocation } from './index';
import { inferTask } from './naming';

export interface WrapOptions {
  /** Explicit workload name; otherwise inferred from the call site. */
  task?: string;
}

type AnyObj = Record<string, any>;

function _record(name: string, params: AnyObj, resp: AnyObj, latencyMs: number): void {
  try {
    const usage = resp?.usage ?? {};
    trackInvocation({
      name,
      model: resp?.model ?? params?.model,
      tokensIn: usage.prompt_tokens,
      tokensOut: usage.completion_tokens,
      cachedTokens: usage?.prompt_tokens_details?.cached_tokens,
      latencyMs,
      responseId: resp?.id,
      metadata: { provider: 'openai', api: 'chat' },
    });
  } catch {
    /* never break the caller */
  }
}

function _recordFailure(name: string, params: AnyObj, err: unknown, latencyMs: number): void {
  try {
    const msg = (err as Error)?.message ?? String(err);
    trackInvocation({
      name,
      model: params?.model,
      latencyMs,
      metadata: {
        provider: 'openai',
        api: 'chat',
        status: 'error',
        error: `${(err as Error)?.name ?? 'Error'}: ${msg}`.slice(0, 300),
      },
    });
  } catch {
    /* ignore */
  }
}

async function* _wrapStream(
  stream: AsyncIterable<AnyObj>,
  name: string,
  params: AnyObj,
  start: number,
  swallow: boolean,
): AsyncGenerator<AnyObj> {
  let usage: AnyObj | undefined;
  let id: string | undefined;
  let model: string | undefined;
  try {
    for await (const chunk of stream) {
      model = chunk?.model ?? model;
      id = chunk?.id ?? id;
      const choices = chunk?.choices ?? [];
      if (chunk?.usage) {
        usage = chunk.usage;
        if (swallow && choices.length === 0) continue; // swallow injected usage-only chunk
      }
      yield chunk;
    }
  } finally {
    try {
      trackInvocation({
        name,
        model: model ?? params?.model,
        tokensIn: usage?.prompt_tokens,
        tokensOut: usage?.completion_tokens,
        cachedTokens: usage?.prompt_tokens_details?.cached_tokens,
        latencyMs: Date.now() - start,
        responseId: id,
        metadata: { provider: 'openai', api: 'chat' },
      });
    } catch {
      /* ignore */
    }
  }
}

/** Wrap an OpenAI client so every chat.completions.create is tracked. Mutates
 * and returns the same client. */
export function wrapOpenAI<T>(client: T, opts: WrapOptions = {}): T {
  const completions: AnyObj | undefined = (client as AnyObj)?.chat?.completions;
  if (!completions || typeof completions.create !== 'function') return client;
  const realCreate = completions.create.bind(completions);

  completions.create = async (params: AnyObj, ...rest: any[]) => {
    const name = inferTask(opts.task);
    const start = Date.now();
    if (params?.stream) {
      const inject = params.stream_options === undefined;
      const p = inject ? { ...params, stream_options: { include_usage: true } } : params;
      let stream: AsyncIterable<AnyObj>;
      try {
        stream = await realCreate(p, ...rest);
      } catch (e) {
        _recordFailure(name, params, e, Date.now() - start);
        throw e;
      }
      return _wrapStream(stream, name, params, start, inject);
    }
    let resp: AnyObj;
    try {
      resp = await realCreate(params, ...rest);
    } catch (e) {
      _recordFailure(name, params, e, Date.now() - start);
      throw e;
    }
    _record(name, params, resp, Date.now() - start);
    return resp;
  };
  return client;
}
