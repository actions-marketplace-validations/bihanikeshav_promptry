import { describe, it, expect, vi, beforeEach } from 'vitest';

const calls: any[] = [];
vi.mock('../src/index', () => ({
  trackInvocation: (opts: any) => {
    calls.push(opts);
  },
}));

import { wrapOpenAI } from '../src/openai';
import { trace } from '../src/naming';

beforeEach(() => {
  calls.length = 0;
});

function nonStreamClient() {
  return {
    chat: {
      completions: {
        create: async (_p: any) => ({
          id: 'resp-1',
          model: 'gpt-4o',
          usage: {
            prompt_tokens: 10,
            completion_tokens: 5,
            prompt_tokens_details: { cached_tokens: 2 },
          },
          choices: [{ message: { content: 'hi' } }],
        }),
      },
    },
  };
}

describe('wrapOpenAI', () => {
  it('records a non-streaming call', async () => {
    const client = wrapOpenAI(nonStreamClient(), { task: 'bot' });
    const resp = await client.chat.completions.create({
      model: 'gpt-4o',
      messages: [{ role: 'user', content: 'q' }],
    });
    expect((resp as any).choices[0].message.content).toBe('hi'); // unchanged
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      name: 'bot',
      model: 'gpt-4o',
      tokensIn: 10,
      tokensOut: 5,
      cachedTokens: 2,
      responseId: 'resp-1',
    });
  });

  it('records failures then rethrows', async () => {
    const client = wrapOpenAI({
      chat: {
        completions: {
          create: async () => {
            throw new Error('boom');
          },
        },
      },
    });
    await expect(
      client.chat.completions.create({ model: 'gpt-4o', messages: [] }),
    ).rejects.toThrow('boom');
    expect(calls[0].metadata.status).toBe('error');
    expect(calls[0].metadata.error).toContain('boom');
  });

  it('captures streaming and swallows the injected usage chunk', async () => {
    async function* chunks() {
      yield { model: 'gpt-4o', id: 's1', choices: [{ delta: { content: 'He' } }] };
      yield { model: 'gpt-4o', id: 's1', choices: [{ delta: { content: 'llo' } }] };
      yield {
        model: 'gpt-4o',
        id: 's1',
        choices: [],
        usage: { prompt_tokens: 8, completion_tokens: 2, prompt_tokens_details: { cached_tokens: 0 } },
      };
    }
    const client = wrapOpenAI({
      chat: { completions: { create: async () => chunks() } },
    });
    const stream = await client.chat.completions.create({
      model: 'gpt-4o',
      messages: [{ role: 'user', content: 'x' }],
      stream: true,
    });
    const got: any[] = [];
    for await (const c of stream as AsyncIterable<any>) got.push(c);
    expect(got.every((c) => c.choices.length > 0)).toBe(true); // usage-only swallowed
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ tokensIn: 8, tokensOut: 2, responseId: 's1' });
  });

  it('attaches the active trace via trackInvocation (opts carry through)', async () => {
    const client = wrapOpenAI(nonStreamClient(), { task: 'bot' });
    await trace('agent', async () => {
      await client.chat.completions.create({ model: 'gpt-4o', messages: [] });
    });
    // trace_id is merged into metadata inside the real trackInvocation; here we
    // just assert the call was recorded within the trace without error.
    expect(calls).toHaveLength(1);
    expect(calls[0].name).toBe('bot');
  });
});
