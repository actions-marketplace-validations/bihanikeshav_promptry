# promptry-js

Lightweight JS/TS client for [promptry](../README.md) telemetry. Ships prompt, invocation, and feedback events to **your own self-hosted** promptry ingest endpoint — the same server your Python apps write to via the `RemoteStorage` backend. Everything lands in the same SQLite store and shows up in the same dashboard (prompts, cost, latency, feedback).

There is no hosted or cloud default endpoint. You always point `endpoint` at a server you run.

Zero runtime dependencies. Works in browsers and Node 18+.

## What it is (and isn't)

Node/Next.js apps use this to report production telemetry so it shows up next to your Python telemetry:

- **`trackPrompt`** — record the prompt/system text a request used.
- **`trackInvocation`** — record one LLM call's cost, latency, tokens, and model.
- **`trackFeedback`** — record an end-user rating/comment, correlated back to an invocation.

It is **not** an eval runner. It does not assert, score, or gate — it only ships telemetry. Run evals with the Python library/CLI; use this to capture what actually happened in production.

## Install

```bash
npm install promptry-js
```

## Usage

### Class API

```typescript
import { Promptry } from 'promptry-js';

const p = new Promptry({
  endpoint: 'https://your-server.com/ingest', // your self-hosted promptry
  apiKey: 'pk_...',        // optional
  projectId: 'my-app',     // optional, added to every event's metadata
  batchSize: 10,            // default 10
  flushInterval: 5000,      // default 5000ms
  sampleRate: 1.0,          // default 1.0
});

// Prompt text — returns content unchanged, ships in the background
const prompt = p.trackPrompt('You are a helpful assistant...', 'rag-qa');

// Retrieval context chunks — returns chunks unchanged, name gets ":context"
const chunks = p.trackContext(retrievedChunks, 'rag-qa');

// One LLM call: cost / latency / tokens
p.trackInvocation({
  name: 'rag-qa',
  model: 'claude-opus-4-8',
  tokensIn: 1200,
  tokensOut: 240,
  cost: 0.018,          // optional — compute it however you like
  latencyMs: 842,
  requestId: 'req-abc', // so feedback can link back to this call
});

// End-user feedback for a prior invocation
p.trackFeedback({
  requestId: 'req-abc',
  rating: 1,            // e.g. thumbs up / down, or 1–5
  comment: 'nailed it',
  source: 'thumbs',
});

await p.flush();    // manual flush
await p.destroy();  // flush + teardown
```

### Singleton API

```typescript
import {
  init, trackPrompt, trackInvocation, trackFeedback, flush,
} from 'promptry-js';

init({ endpoint: 'https://your-server.com/ingest' });

trackPrompt(systemPrompt, 'rag-qa');
trackInvocation({ name: 'rag-qa', tokensIn: 1200, tokensOut: 240, requestId: 'req-abc' });
trackFeedback({ requestId: 'req-abc', rating: 1 });

await flush();
```

## Wire contract

Every batch this client POSTs conforms to the shared JSON Schema at
[`docs/wire-schema/events.schema.json`](../docs/wire-schema/events.schema.json) —
the single source of truth for the envelope and event types, shared with the
Python `RemoteStorage._ship_batch` backend. Both the Python test
(`tests/test_wire_contract.py`) and the JS test
(`__tests__/wire-contract.test.ts`) validate their payloads against that file.

```json
{
  "events": [
    {
      "type": "invocation",
      "data": {
        "name": "rag-qa",
        "model": "claude-opus-4-8",
        "tokens_in": 1200,
        "tokens_out": 240,
        "cost": 0.018,
        "latency_ms": 842,
        "request_id": "req-abc",
        "metadata": { "project_id": "my-app" },
        "created_at": "2026-07-07T14:23:45.123Z"
      },
      "timestamp": "2026-07-07T14:23:45.123Z"
    }
  ]
}
```

Batching, retry, and offline fallback mirror the Python client: events are
queued, flushed on `batchSize`/`flushInterval`, retried with exponential
backoff, and (in the browser) persisted to `localStorage` if the network is
down.

## Development

```bash
npm install
npm run build
npm test
```
