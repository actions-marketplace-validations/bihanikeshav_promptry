# Integrating promptry into an existing RAG / LLM platform

> A step-by-step guide written for a coding agent. The goal: add promptry's
> observability, prompt management, and eval suites to an **existing** Python
> app **without rewriting it**. Every step is opt-in and additive — adopt them
> in order, stopping wherever the value runs out for this codebase.

promptry is local-first: one SQLite file, no account, no required network. The
library only writes telemetry you explicitly hand it, and the dashboard is a
read-only-ish FastAPI app over that same file.

---

## 0. Orient yourself in the target codebase

Before changing anything, find and note:

- **Where LLM calls happen** (e.g. `openai.chat.completions.create`, a
  `litellm.completion` wrapper, an `llm_service.py`). These are your
  instrumentation points.
- **Where prompts live** (inline f-strings? a `prompts.py`? a templates dir?).
- **Where retrieval happens** (the RAG context assembly), if any.
- **How requests are identified** (a request id / trace id you can thread
  through to correlate end-user feedback later).
- **The test/CI setup** (pytest? a GitHub Actions workflow?).

```bash
pip install promptry
```

Pick ONE representative LLM call to instrument first. Get it working end to end
(step 1 + step 7) before fanning out to the rest.

---

## 1. Track invocations — the cost & telemetry ledger

`track_invocation()` records one row per LLM call: cost, tokens, latency, the
model, and (optionally) the request/response text. It returns `None` and never
raises into your request path — wrap your existing call, don't replace it.

Build the metadata from whatever your provider's response already gives you:

```python
import time
from promptry import track_invocation

def call_llm(messages, model="gpt-4o-mini", request_id=None):
    t0 = time.perf_counter()
    resp = client.chat.completions.create(model=model, messages=messages)  # unchanged
    latency_ms = (time.perf_counter() - t0) * 1000

    usage = resp.usage
    track_invocation(
        name="rag.answer",                 # logical prompt/step name (see naming below)
        metadata={
            "model": model,
            "tokens_in": usage.prompt_tokens,
            "tokens_out": usage.completion_tokens,
            "latency_ms": latency_ms,
            # "cost" is optional — promptry computes it from its rate table if
            # the model is known. Pass it if you already have it.
        },
        input_text=messages[-1]["content"],          # optional, for the trace viewer
        output_text=resp.choices[0].message.content,  # optional
        capture=True,        # opt in to storing the text above
        sample_rate=0.1,     # store text for ~10% of calls to keep the DB lean
        request_id=request_id,  # thread your app's id so feedback can link back
    )
    return resp
```

**Naming convention:** use `module.step` names (`rag.answer`, `rag.rerank`,
`agent.planner`). The dashboard groups cost by the part before the first dot,
so good names give you a free per-module cost breakdown.

**Verify:** make one call, then check the ledger:

```bash
python -c "from promptry.storage import get_storage as g; print(g().list_invocations(days=1, limit=5))"
```

You should see your row with tokens/cost. That alone powers the Cost, Drift,
and Traces views. **You can stop here** if all you wanted was cost/usage
observability.

---

## 2. Adopt the prompt CMS (optional but high-value)

Move prompts out of code so they can be edited from the dashboard, versioned,
diffed, and promoted dev→staging→prod — **without a redeploy**.

Replace an inline prompt string with `render_prompt(name, default, **vars)`.
The in-code string stays as the fallback (so nothing breaks if the DB is
empty), and `seed_prompt` registers it as version 1 on first run.

```python
from promptry import render_prompt

SYSTEM_DEFAULT = """You are a support assistant for {{product}}.
Answer using ONLY the context below. If unsupported, say "I don't know."

Context:
{{context}}"""

system = render_prompt(
    "rag.system",          # registry name — shows up in the dashboard
    SYSTEM_DEFAULT,        # fallback + seeded as v1
    product="Acme",
    context=retrieved_chunks_joined,
    env="prod",            # serve the version tagged 'prod' (omit for latest)
)
```

**Variable syntax:** use `{{name}}`. (Legacy `$name` / `${name}` still renders
for backward compatibility, but `{{}}` is preferred — it won't collide with a
literal `$` in the prompt body, e.g. prices or regex.) Unknown placeholders are
left intact rather than raising, so a malformed dashboard edit can't crash a
request.

Do this only for the prompts you actually want editable. Everything else keeps
working untouched.

---

## 3. Wire an LLM judge (needed for `assert_llm` / `assert_grounded`)

Several assertions grade output with an LLM. promptry is provider-agnostic: you
hand it a callable `(prompt: str) -> str`. Set it once at startup.

```python
from promptry import set_judge

def judge(prompt: str) -> str:
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return r.choices[0].message.content

set_judge(judge)
```

Tell the dashboard which model the judge uses (for cost attribution) in
`.promptry/config.toml` — see step 6:

```toml
[judge]
model = "gpt-4o-mini"
```

---

## 4. Write eval suites

A suite is plain Python: a function decorated with `@suite` that calls
assertions. Put them in an `evals.py` (or a `tests/` module). For RAG, the
high-signal assertions are semantic similarity, grounding (no fabrication), and
schema/JSON shape.

```python
from promptry import suite, assert_semantic, assert_contains, assert_grounded

GOLDEN = [
    {"q": "What's the refund window?", "ctx": "Refunds within 30 days.",
     "expect": "30 days"},
]

@suite("rag-quality")
def rag_quality():
    for case in GOLDEN:
        answer = my_pipeline(case["q"], case["ctx"])   # call the REAL pipeline
        assert_contains(answer, [case["expect"]])
        assert_semantic(answer, case["expect"], threshold=0.7)
        assert_grounded(answer, case["ctx"], threshold=0.8)   # needs a judge (step 3)
```

Tip: instead of hand-writing golden cases, promote real production traces into
a per-prompt **eval set** from the dashboard (the invocation page → "Add to
eval set"), then re-run them against any model to check accuracy.

---

## 5. Run in CI

`promptry run` exits non-zero on a regression — drop it into CI to gate merges.

```bash
promptry run rag-quality --module evals --compare prod --markdown pr.md
```

- `--module evals` imports the module that defines the suite.
- `--compare prod` diffs against the prod-tagged baseline and fails on
  regressions.
- `--markdown pr.md` writes a summary you can post as a PR comment.

**Performance budgets (SLO gates):** add to `.promptry/config.toml` to fail CI
when calls get too slow, independent of the score:

```toml
[slo]
max_latency_ms = 8000
p95_latency_ms = 5000
```

A minimal GitHub Actions step:

```yaml
- run: pip install promptry && promptry run rag-quality --module evals --compare prod
```

---

## 6. Project config + dashboard

`.promptry/config.toml` lives in your repo root (the process CWD) and is
committed so the team shares model lists, judge settings, dashboard prefs, and
pricing overrides. **API keys are never stored here** — they're read from env
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …); the dashboard only reports which
providers have a key set.

```toml
[[models]]
id = "gpt-4o-mini"
provider = "openai"
label = "GPT-4o mini"

[dashboard]
default_days = 14
```

Launch the dashboard against your app's SQLite file:

```bash
promptry dashboard            # serves the UI + /api on localhost:8420
```

It reads the same DB your app writes to (default `~/.promptry/promptry.db`;
point `PROMPTRY_DB` at a project-local file if you prefer).

---

## 7. Close the loop: ingest end-user feedback

If your app collects thumbs-up/down or ratings, send them back keyed by the
`request_id` you passed in step 1, so a rating links to the exact call:

```bash
# Use the dashboard origin you actually open (local, tunnel, or reverse-proxy host).
# When PROMPTRY_AUTH_TOKEN is set, also send: -H "Authorization: Bearer $PROMPTRY_AUTH_TOKEN"
curl -X POST "$PROMPTRY_URL/api/feedback" -H 'Content-Type: application/json' \
  -d '{"request_id": "abc123", "rating": 0.0, "comment": "wrong figure"}'
# local default: PROMPTRY_URL=http://localhost:8420
```

Ratings then show on the invocation, feed the online-drift signal, and let you
filter traces by low ratings to build eval cases from real failures.

---

## 8. Dashboard auth (when reverse-proxied)

The process always binds `127.0.0.1`. If you put nginx/Caddy in front of a public
hostname, set **one shared secret** for the whole deployment:

```bash
export PROMPTRY_AUTH_TOKEN="$(openssl rand -hex 32)"   # put in vault + systemd EnvironmentFile
promptry dashboard --port 8420 --no-open
```

- **Model:** single API key for everyone (not per-user tokens). Rotate once → everyone re-enters the new value; all session cookies invalidate.
- **Browser:** login form → HttpOnly session cookie (**7 days**).
- **Machines / feedback curl:** `Authorization: Bearer $PROMPTRY_AUTH_TOKEN`.
- **Distribution:** password manager / team vault. No public “issue me a token” route.
- **Rotate:** rewrite the env file, restart the unit, update the vault.

Unset `PROMPTRY_AUTH_TOKEN` = open API (fine only while localhost-only).

## 9. Price feed (optional)

Cost math uses a bundled rate table. For fresher numbers:

```bash
promptry prices --refresh          # pull published prices.json → ~/.promptry/prices.json
# or let the dashboard do it: on start + every 24h (PROMPTRY_PRICES_AUTO_REFRESH=0 to disable)
```

The published file lives in the promptry repo and is refreshed by CI; your
server does **not** call OpenAI/Anthropic pricing APIs itself.

## Agent checklist

- [ ] `pip install promptry`; identify LLM call sites, prompt locations, request id.
- [ ] Wrap ONE call with `track_invocation` (model/tokens/latency); verify a row lands.
- [ ] Roll `track_invocation` out to the other call sites with `module.step` names.
- [ ] (Optional) Convert chosen prompts to `render_prompt` with `{{vars}}`.
- [ ] (If using judge assertions) `set_judge(...)` at startup + `[judge] model` in config.
- [ ] Add an `evals.py` with a `@suite` that runs the real pipeline + assertions.
- [ ] Add `promptry run … --compare prod` to CI; optionally `[slo]` budgets.
- [ ] Commit `.promptry/config.toml`; set provider keys in the environment.
- [ ] If the dashboard is public: set `PROMPTRY_AUTH_TOKEN`, store in vault, document rotate.
- [ ] (Optional) POST feedback with the `request_id` to close the loop.

Keep each change small and verifiable. promptry is designed so you can adopt
step 1 alone and add the rest later — never a big-bang migration.
