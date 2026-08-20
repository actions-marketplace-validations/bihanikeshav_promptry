import { describe, it, expect } from 'vitest';
import { task, trace, currentTrace, inferTask } from '../src/naming';

describe('inferTask precedence', () => {
  it('explicit wins', () => {
    expect(inferTask('checkout')).toBe('checkout');
  });

  it('ambient task() overrides', () => {
    const inside = task('billing', () => inferTask());
    expect(inside).toBe('billing');
    // restored outside the block
    expect(inferTask()).not.toBe('billing');
  });

  it('explicit beats ambient', () => {
    expect(task('billing', () => inferTask('explicit'))).toBe('explicit');
  });

  it('falls back to a call-site name', () => {
    const name = inferTask();
    expect(typeof name).toBe('string');
    expect(name.length).toBeGreaterThan(0);
  });
});

describe('trace call trees', () => {
  it('nesting shares the outer trace id', () => {
    expect(currentTrace()).toBeUndefined();
    trace('outer', (outer) => {
      expect(currentTrace()).toEqual({ traceId: outer, span: 'outer' });
      trace('inner', (inner) => {
        expect(inner).toBe(outer); // nested shares the id
        expect(currentTrace()).toEqual({ traceId: outer, span: 'inner' });
      });
      expect(currentTrace()?.span).toBe('outer');
    });
    expect(currentTrace()).toBeUndefined();
  });

  it('survives async boundaries', async () => {
    await trace('async_agent', async (tid) => {
      await Promise.resolve();
      expect(currentTrace()?.traceId).toBe(tid);
    });
  });
});
