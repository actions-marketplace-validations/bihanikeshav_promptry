# Instrumenting an app with promptry (agent instructions)

You are adding promptry to an existing LLM/RAG app so that (a) every LLM call is
auto-tracked — prompt text, cost, latency, tokens, RAG context — and (b) the
prompts become manageable from the promptry dashboard: edited, versioned,
diffed, and promoted without a redeploy. Both depend on one thing: the app and
the dashboard must read/write the **same prompt database**. Instrument one call
site end to end first, verify, then fan out.

## Step 0 — Point everything at ONE shared prompt DB (non-negotiable)

promptry storage is a single SQLite file selected by the `PROMPTRY_DB`
environment variable (default: `~/.promptry/promptry.db`). Every process — the
app, the `promptry` CLI, and `promptry dashboard` — must resolve to the SAME
file.

```bash
# Set in the app's environment AND the shell where you run the dashboard/CLI:
export PROMPTRY_DB=/srv/myapp/promptry.db     # Windows: $env:PROMPTRY_DB = "E:\myapp\promptry.db"
```

> WARNING: if the app writes to one DB and the dashboard reads another, nothing
> errors — the dashboard just shows no data, and dashboard prompt edits never
> reach the app. This is the #1 integration failure. Verify with the checklist
> at the end before declaring success.

Multi-process / multi-host apps: point every instance's `PROMPTRY_DB` at one
shared path (e.g. a mounted volume). Browser/edge/JS apps cannot open SQLite —
use `promptry-js` (below), which POSTs telemetry over HTTP to a self-hosted
promptry server's ingest endpoint; the server owns the SQLite file.

## Step 1 — Install

```bash
pip install promptry
```

## Step 2 — Instrument an LLM call site

Three changes per call site: `render_prompt` replaces the hardcoded prompt
string, `track_invocation` lands right after the model call, and `seed_prompt`
runs once at startup.

**Before:**

```python
def answer(question, context):
    prompt = f"""You are a support assistant.
Answer using ONLY the context below. If unsupported, say "I don't know."

Context:
{context}

Question: {question}"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content
```

**After:**

```python
import time
from promptry import render_prompt, seed_prompt, track_invocation

# Module-level default. Placeholders use Python string.Template syntax:
# $name / ${name} — NOT f-strings, NOT {braces}. Escape a literal $ as $$.
DEFAULT_ANSWER = """You are a support assistant.
Answer using ONLY the context below. If unsupported, say "I don't know."

Context:
$context

Question: $question"""

# Call once at app startup. Registers the in-code default as version 1 only
# if the prompt doesn't exist yet — it NEVER overwrites a dashboard edit.
seed_prompt("rag.answer", DEFAULT_ANSWER)

def answer(question, context, request_id=None):
    # Fetches the latest managed version from the shared DB (cached, default
    # ttl=60s) and substitutes $placeholders. Falls back to DEFAULT_ANSWER on
    # any miss — never raises. This line is what makes dashboard edits live.
    prompt = render_prompt("rag.answer", DEFAULT_ANSWER,
                           question=question, context=context)

    t0 = time.perf_counter()
    resp = client.chat.completions.create(          # your call, unchanged
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    # ONE call to track_invocation per LLM call. This is the invocations
    # ledger — every call is its own row — and it is what powers the Cost
    # dashboard. (track() dedups by content hash; it is NOT the cost path.)
    usage = resp.usage
    track_invocation(
        "rag.answer",
        metadata={
            "model": "gpt-4o-mini",
            "tokens_in": usage.prompt_tokens,
            "tokens_out": usage.completion_tokens,
            "latency_ms": latency_ms,
            # "cost": 0.0012,  # optional — promptry computes it from its rate
            #                  # table when model + token counts are present
        },
        input_text=prompt,
        output_text=resp.choices[0].message.content,
        capture=True, sample_rate=0.1,   # store text for ~10% of calls
        request_id=request_id,           # correlates end-user feedback later
    )
    return resp.choices[0].message.content
```

Rules:
- Name prompts `module.step` (`rag.answer`, `agent.planner`) — the Cost view
  groups by the part before the first dot.
- To serve the promoted production version instead of the latest edit, pass
  `env="prod"` to `render_prompt` (promote versions from the dashboard).
- `track_invocation` accepts token keys as `tokens_in`/`tokens_out`, or the
  SDK-native `input_tokens`/`output_tokens` (Anthropic) and
  `prompt_tokens`/`completion_tokens` (OpenAI).
- Roll out to every LLM call site once the first one verifies.

## Step 3 — RAG: wrap retrieval with track_context

`track_context(chunks, name)` returns the chunks unchanged and records them so
the dashboard can show what context each prompt saw.

```python
from promptry import track_context

chunks = retriever.search(question, k=5)                 # unchanged
chunks = track_context([c.text for c in chunks], "rag.answer")
context = "\n\n".join(chunks)
```

## Step 4 — promptry.toml + API keys

One canonical `promptry.toml` at the project root (a legacy
`.promptry/config.toml` is still merged for back-compat; prefer
`promptry.toml`). Minimal example:

```toml
[storage]
db_path = "/srv/myapp/promptry.db"   # or leave unset and rely on PROMPTRY_DB

[judge]
model = "gpt-4o-mini"                # used by LLM-judge assertions + dataset gen

[[models]]                           # populates the dashboard Playground/compare
id = "gpt-4o-mini"
provider = "openai"
label = "GPT-4o mini"
```

API keys: promptry never reads, asks for, or stores model API keys. Model
calls go through litellm, which reads the standard per-provider env var —
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`
(Google), `AZURE_OPENAI_API_KEY`. Export the right one in the app's
environment; promptry infers the provider from the model id. Keys stay in env
— never in `promptry.toml`, never in the DB.

If a key lives under a non-standard variable name, alias it in `[keys]` — the
env-var NAME only, never the secret:

```toml
[keys]
openai = "MY_OPENAI_KEY"   # promptry bridges this to OPENAI_API_KEY at call time
```

The dashboard's Settings page auto-detects keys and lets the user click an
undetected provider to enter its variable name.

## Step 5 — turn logged traffic into an eval suite (optional)

Once invocations, feedback, and context are flowing, the same data seeds
regression tests. Over MCP (`claude mcp add promptry -- promptry mcp`):

1. `list_suite_candidates(source="feedback")` — cases from positively-rated
   invocations (`source="golden"` for saved golden examples). Question/response
   need `track_invocation(capture=True)`; context needs `track_context`.
2. `create_eval_suite(name, cases, model=..., prompt=...)` — writes a runnable
   suite into `evals.yaml`. Each case is `{input, context?, expect:
   [{type, value}]}` (assertion types: contains, not_contains, regex, exact,
   semantic, grounded, llm); a case's `context` auto-becomes a grounded
   assertion. Use `pipeline` instead of `model`/`prompt` to call the app's own
   pipeline.
3. `run_eval(name)` — runs it. The suite appears on the dashboard's Evals page
   and stays editable there.

The same flow exists in the dashboard (Evals → **New suite**) and the CLI
(`promptry new suite`) — all three write the same `evals.yaml`.

## JS/TS apps

Use `promptry-js` (`npm install promptry-js`). It is the write path only —
telemetry is POSTed over HTTP to a self-hosted promptry server's ingest
endpoint (the server owns the SQLite; the JS client never opens it).
Evaluation, the CMS, and the dashboard stay in Python.

```typescript
import { init, trackPrompt, trackInvocation, trackFeedback } from 'promptry-js';

init({ endpoint: 'https://promptry.internal/ingest', apiKey: 'pk_...' }); // apiKey optional

const prompt = trackPrompt(systemPrompt, 'rag.answer');    // returns text unchanged
trackInvocation({ name: 'rag.answer', model: 'gpt-4o-mini',
                  tokensIn: 1200, tokensOut: 240, latencyMs: 842,
                  requestId: 'req-abc' });
trackFeedback({ requestId: 'req-abc', rating: 1 });
```

There is also `trackContext(chunks, name)` for RAG retrieval.

## Verification checklist

Run through ALL of these before reporting the integration done:

- [ ] `echo $PROMPTRY_DB` prints the same path in the app's environment and in
      the shell running the dashboard/CLI.
- [ ] Start the app, trigger one instrumented call.
- [ ] `promptry dashboard` (same `PROMPTRY_DB`) shows the prompt (e.g.
      `rag.answer`) in the prompt list — proof that `seed_prompt` reached the
      shared DB.
- [ ] The Cost view shows the invocation with model, tokens, latency, and a
      nonzero cost — proof `track_invocation` metadata is correct. If cost is
      $0, the model id has no rate-table entry; add a `[pricing.*]` override.
- [ ] Edit the prompt text in the dashboard, wait past the cache TTL (default
      60s) or restart the app, trigger the call again, and confirm the model
      received the NEW text — proof the CMS round-trip is live.
- [ ] For RAG: the invocation shows tracked context chunks.
- [ ] JS path only: the server ingest endpoint returns 2xx and events appear
      in the same dashboard.
