import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import { Promptry } from '../src/promptry';
import type { EventBatch } from '../src/types';

// The single shared wire contract — same file the Python test validates against.
const __dirname = dirname(fileURLToPath(import.meta.url));
const schemaPath = resolve(
  __dirname,
  '../../docs/wire-schema/events.schema.json',
);
const schema = JSON.parse(readFileSync(schemaPath, 'utf-8'));

const ajv = new Ajv2020({ strict: false });
const validate = ajv.compile(schema);

// Capture all POSTed batches
const sentBatches: EventBatch[] = [];

vi.mock('../src/transport', () => ({
  sendBatch: vi.fn(async (batch: EventBatch) => {
    sentBatches.push(batch);
    return true;
  }),
}));

vi.mock('../src/storage', () => ({
  createOfflineStore: () => ({
    load: () => [],
    save: () => {},
    clear: () => {},
  }),
}));

function assertValid(batch: EventBatch): void {
  const ok = validate(batch);
  if (!ok) {
    throw new Error(
      'wire-contract violation: ' + JSON.stringify(validate.errors, null, 2),
    );
  }
  expect(ok).toBe(true);
}

describe('wire contract', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sentBatches.length = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('schema compiles as draft 2020-12', () => {
    expect(typeof validate).toBe('function');
  });

  it('trackPrompt payload validates against the schema', async () => {
    const p = new Promptry({
      endpoint: 'http://test/ingest',
      batchSize: 100,
      flushInterval: 60_000,
    });
    p.trackPrompt('You are a helpful assistant', 'rag-qa', {
      metadata: { env: 'prod' },
    });
    await p.flush();

    expect(sentBatches.length).toBeGreaterThanOrEqual(1);
    sentBatches.forEach(assertValid);
    const events = sentBatches.flatMap((b) => b.events);
    expect(events[0].type).toBe('prompt_save');
    await p.destroy();
  });

  it('trackInvocation payload validates against the schema', async () => {
    const p = new Promptry({
      endpoint: 'http://test/ingest',
      projectId: 'my-app',
      batchSize: 100,
      flushInterval: 60_000,
    });
    p.trackInvocation({
      name: 'rag-qa',
      model: 'claude-opus-4-8',
      tokensIn: 120,
      tokensOut: 55,
      cost: 0.0123,
      latencyMs: 842.1,
      requestId: 'req-1',
      metadata: { route: '/ask' },
    });
    await p.flush();

    sentBatches.forEach(assertValid);
    const ev = sentBatches.flatMap((b) => b.events)[0];
    expect(ev.type).toBe('invocation');
    if (ev.type === 'invocation') {
      expect(ev.data.tokens_in).toBe(120);
      expect(ev.data.tokens_out).toBe(55);
      expect(ev.data.model).toBe('claude-opus-4-8');
      expect(ev.data.request_id).toBe('req-1');
      expect(ev.data.metadata).toEqual({ route: '/ask', project_id: 'my-app' });
    }
    await p.destroy();
  });

  it('trackInvocation validates with only required fields', async () => {
    const p = new Promptry({
      endpoint: 'http://test/ingest',
      batchSize: 100,
      flushInterval: 60_000,
    });
    p.trackInvocation({ name: 'minimal' });
    await p.flush();

    sentBatches.forEach(assertValid);
    const ev = sentBatches.flatMap((b) => b.events)[0];
    if (ev.type === 'invocation') {
      expect(ev.data.tokens_in).toBe(0);
      expect(ev.data.tokens_out).toBe(0);
      expect(ev.data.metadata).toEqual({});
    }
    await p.destroy();
  });

  it('trackFeedback payload validates against the schema', async () => {
    const p = new Promptry({
      endpoint: 'http://test/ingest',
      batchSize: 100,
      flushInterval: 60_000,
    });
    p.trackFeedback({
      requestId: 'req-1',
      rating: 1,
      comment: 'great answer',
      source: 'thumbs',
    });
    await p.flush();

    sentBatches.forEach(assertValid);
    const ev = sentBatches.flatMap((b) => b.events)[0];
    expect(ev.type).toBe('feedback');
    if (ev.type === 'feedback') {
      expect(ev.data.request_id).toBe('req-1');
      expect(ev.data.rating).toBe(1);
      expect(ev.data.comment).toBe('great answer');
    }
    await p.destroy();
  });

  it('trackFeedback validates with only requestId', async () => {
    const p = new Promptry({
      endpoint: 'http://test/ingest',
      batchSize: 100,
      flushInterval: 60_000,
    });
    p.trackFeedback({ requestId: 'req-2' });
    await p.flush();

    sentBatches.forEach(assertValid);
    await p.destroy();
  });

  it('a mixed batch of all three event types validates', async () => {
    const p = new Promptry({
      endpoint: 'http://test/ingest',
      batchSize: 100,
      flushInterval: 60_000,
    });
    p.trackPrompt('sys prompt', 'rag-qa');
    p.trackInvocation({ name: 'rag-qa', tokensIn: 10, tokensOut: 20, requestId: 'r1' });
    p.trackFeedback({ requestId: 'r1', rating: -1 });
    await p.flush();

    sentBatches.forEach(assertValid);
    const types = sentBatches.flatMap((b) => b.events).map((e) => e.type).sort();
    expect(types).toEqual(['feedback', 'invocation', 'prompt_save']);
    await p.destroy();
  });
});
