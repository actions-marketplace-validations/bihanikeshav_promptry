# promptry

[![PyPI](https://img.shields.io/pypi/v/promptry)](https://pypi.org/project/promptry/)
[![npm](https://img.shields.io/npm/v/promptry-js)](https://www.npmjs.com/package/promptry-js)
[![CI](https://github.com/bihanikeshav/promptry/actions/workflows/ci.yml/badge.svg)](https://github.com/bihanikeshav/promptry/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Local-first prompt observability that lives in your repo.** Version your prompts, write eval suites in YAML or Python, track the cost of every call, edit prompts live, and catch regressions in CI. One `pip install`, one SQLite file, zero services — your prompts never leave your laptop.

**[Try the live demo →](https://promptry.meownikov.xyz/demo/)** · [Integration guide](docs/INTEGRATION.md) · [Docs](https://promptry.meownikov.xyz/docs.html)

```bash
pip install promptry
```

## Quick start

**1. See what every call costs — change one import.**

```python
from promptry.openai import OpenAI   # was: from openai import OpenAI

client = OpenAI()
client.chat.completions.create(model="gpt-4o-mini", messages=[...])
# ^ recorded: cost, tokens, cached-token split, latency, model, call-site name.
# Streaming, failures, embeddings, and the Responses API are captured too.
```

```bash
promptry dashboard          # cost, drift, and per-call traces at 127.0.0.1:8420
```

Not on the OpenAI SDK? `track_invocation(name, metadata=...)` records the same
row from any provider's response. On the frontend, `wrapOpenAI(new OpenAI())`
from [`promptry-js`](promptry-js/) ships the same events to the same store.

**2. Catch regressions before they ship — write an eval.**

```python
from promptry import suite, assert_semantic

@suite("rag-regression")           # a plain function; runs via CLI or in CI
def test_quality():
    response = my_pipeline("What is photosynthesis?")
    assert_semantic(response, "Converts light into chemical energy")
```

```bash
promptry init                            # scaffold project + a starter eval
promptry run rag-regression --module evals
```

When a suite regresses against its baseline, promptry reports **what** changed —
not just that it broke:

```
Overall score: 0.910 -> 0.720  REGRESSION

Probable cause:
  -> Prompt changed (v3 -> v4)
```

Prefer no code? `promptry new suite --yaml` scaffolds a declarative `evals.yaml`
from `--case` flags. Both paths run through the same CLI.

## Install extras

Core stays small on purpose — CLI, prompt registry, deterministic assertions
(exact / text / schema / JSON / rouge / levenshtein), drift, cost tracking from
the bundled price snapshot, the local **dashboard**, and the MCP server. Add an
extra only when you need it:

```bash
pip install 'promptry[semantic]'   # assert_semantic, embedding distance, RAG context, clustering (sentence-transformers + chromadb)
pip install 'promptry[llm]'        # run real model completions / assert_llm judging / live price refresh (litellm + openai + anthropic)
pip install 'promptry[postgres]'   # point the store at a shared PostgreSQL server (alpha) instead of the default SQLite file
pip install 'promptry[full]'       # everything
```

If you call a feature whose extra isn't installed, promptry raises a clear error
telling you the exact `pip install` to run — it never fails with a bare
`ModuleNotFoundError`.

## Features

| Feature | What it does |
|---------|--------------|
| **Prompt versioning** | Content-hashed, automatic dedup, grouped by module. No manual bumps, no YAML, no git dance. |
| **Live prompt CMS** | `render_prompt()` serves dashboard-edited `{{name}}` templates with no redeploy. Edit a prompt in the browser, your app picks it up on the next call. Substitution is value-driven, so JSON braces and literal `$` are never mistaken for variables. |
| **Semantic prompt search** | Search the registry by meaning (`promptry prompt search`) and flag near-duplicate prompts (`promptry prompt duplicates`, likely forks to consolidate). Embeddings with a lexical fallback. |
| **Cache optimization** | `promptry cache` (CLI) and the dashboard's Cache optimization page, all local: **reorder inputs** ranks prompts by prefix-cache reorder gain, **consolidate** diffs near-duplicate prompts and can apply one's wording to the other, **shorten** flags redundant/filler wording and estimates the tokens saved. |
| **Environment promotion** | dev → staging → prod tags gate every edit before it reaches users. Promote a version, roll one back. |
| **YAML or Python suites** | Declarative `evals.yaml` is the no-code default; `@suite` decorators are the power path for custom pipelines and judges, with full IDE/debugger support. Both are first-class and run through the same CLI. Scaffold either with `promptry new suite`, or build one in the dashboard. |
| **Deterministic assertions** | Semantic, schema, JSON, regex, grounding, tool-use, exact match, Levenshtein, ROUGE-L, embedding distance. Zero API calls at CI time. |
| **LLM-as-judge** | Opt-in, not default. Auto-configures from `[judge] model` in `promptry.toml`, or set your own callable via `set_judge()`. |
| **Drift detection** | Mann-Whitney U on a rolling window with real p-values — on eval scores *and* on live production telemetry (cost, latency, output length, rating). |
| **Regression diff** | Tells you *what* changed — prompt version, model, or data — not just that it broke. |
| **Regression bisect** | Walks the run history to pinpoint the first run that broke a test. |
| **SLO gates** | `[slo]` latency budgets fail CI on performance regressions, independent of the eval score. |
| **Judge-cost attribution** | LLM-judge spend estimated and summed per eval run, so you see what evaluation itself costs. |
| **Eval-from-trace** | Promote a real captured invocation into a per-prompt golden set, then re-run it against any model to check accuracy. |
| **Model comparison** | Statistical comparison against the historical baseline, not snapshot-to-snapshot. |
| **Invocations ledger** | Every call recorded: tokens, cost, latency, model. Opt-in sampled request/response trace capture; per-call ratings/feedback via `POST /api/feedback`. |
| **Cost tracking** | Per-model pricing with module → prompt → call drill-down, per-call template-vs-payload split, and a coverage check that flags un-priced models. Cache-aware, across OpenAI, Anthropic, Gemini, Grok. |
| **Call traces** | Wrap an agent run in `with promptry.trace("agent"):` (or `trace()` in JS) and the dashboard shows a per-step cost waterfall — which step of a multi-call agent burned the tokens. Metadata on invocations, not distributed tracing; still one SQLite file. |
| **Price feed** | **[LiteLLM](https://github.com/BerriAI/litellm) `model_cost`** is the catalog (MIT) — we snapshot and serve it, we do not curate our own list. CI publishes `prices.json`; package ships a snapshot; dashboard auto-pulls every 24h. |
| **Budgets & alerts** | Daily and monthly spend caps with breach alerts, fanned out to Slack/webhook, PagerDuty, or email. Configure and fire a test alert from the dashboard's Settings page. |
| **PII / secret scanning** | Captured request/response text is scanned for API keys, private keys, JWTs, emails, SSNs, and card numbers; the dashboard warns with masked findings. |
| **Safety suite** | 25 jailbreak / injection / PII / encoding templates across 6 categories. Extensible via `templates.toml`. |
| **MCP server** | First-class: your LLM agent drives the whole test runner — and can *create* eval suites from real logged traffic (`list_suite_candidates` → `create_eval_suite` → `run_eval`), with everything landing on the dashboard. Native, not a plugin. |
| **Dashboard** | Local web UI for eval history, an in-UI suite creator/editor, prompt registry + live editing, cost drill-down, model comparison, invocation traces, and a multi-model playground. No account, no cloud. |
| **Project config** | Committable `promptry.toml` (models, judge, dashboard prefs, pricing overrides, `[keys]` env-var aliases). API keys via env — never in config or the DB. |
| **JS/TS client** | Ship prompt events from frontend/Node apps to the same SQLite store, over the same [wire schema](docs/wire-schema/events.schema.json) the Python client uses. |
| **CI-friendly output** | `--format json\|junit` on `run`, `compare`, and `drift`, plus `--output` to write the report to a file. |
| **Prompt linting** | `promptry lint` flags placeholder/format footguns in a saved prompt or file; exits 1 on error-level findings. |
| **Failure clustering** | `promptry cluster` groups a suite's recent failed assertions into patterns. |
| **PII/secret scan** | `promptry scan` regex-tripwires captured invocation text for secrets/PII; `--fail-on-hit` gates CI. |
| **Production replay** | `promptry replay` runs captured production inputs through the current pipeline and diffs against the recorded output. |
| **Golden-set drift** | `promptry golden` re-runs a prompt's golden examples through a model and scores drift vs. the recorded reference. |
| **Setup checks** | `promptry doctor` checks dependencies, config, and storage; exits 1 if anything's broken. |

## Dashboard

```bash
promptry dashboard
```

Binds **127.0.0.1** only. Reverse-proxy it for remote access, then **lock it** with a single shared secret (one API key for the whole team — not per-user tokens):

```bash
# generate once; put in a password manager / team vault
export PROMPTRY_AUTH_TOKEN="$(openssl rand -hex 32)"
promptry dashboard --no-open
```

| Piece | Lifetime | Notes |
|-------|----------|--------|
| **`PROMPTRY_AUTH_TOKEN`** | Until you rotate it | Shared by everyone; paste at login or send as `Authorization: Bearer …` |
| **Session cookie** | **7 days** | Issued after browser login; re-enter the same secret when it expires |
| **Rotate** | — | Change the env var + restart → **all** sessions die; update the vault once |

There is no self-serve “mint me a token” endpoint (that would defeat the lock). Distribute the secret via vault; operators with shell can read the env file. See [Dashboard auth](docs/guide.md#dashboard-auth).

**Prices:** the dashboard pulls the published feed ([`prices.json`](prices.json) on `main`) at startup and every 24h. Opt out with `PROMPTRY_PRICES_AUTO_REFRESH=0`. Manual: `promptry prices --refresh`.

Eval health and spend at a glance — drill into evals or cost for detail.
![Overview](docs/screenshots/dashboard-overview.png)

The prompt registry, grouped by module. Click any prompt to inspect versions, diffs, and stats.
![Prompts](docs/screenshots/dashboard-prompts.png)

A prompt detail view: edit the live `$`-placeholder template, with variable pills and promotion tags.
![Prompt detail](docs/screenshots/dashboard-prompt-detail.png)

Cost, drilled module → prompt → the priciest individual calls.
![Cost](docs/screenshots/dashboard-cost.png)

A single call, broken into fixed template overhead vs the variable payload you fed in.
![Invocation](docs/screenshots/dashboard-invocation.png)

The playground: render a prompt and compare it across models before promoting to a suite.
![Playground](docs/screenshots/dashboard-playground.png)

The Evals page also builds suites: **New suite** assembles a YAML suite from manual cases, golden examples, or positive-feedback logs, and **Edit** reopens any YAML-declared suite in the same builder (Python-defined suites are read-only). A Cache optimization page (also `promptry cache` on the CLI) ranks prompts by prefix-cache reorder opportunity, diffs near-duplicate prompts for consolidation, and flags redundant/filler wording to shorten — all read from your local prompt registry and invocations ledger, no LLM calls. See the [guide](docs/guide.md#cache-optimization).

## Why promptry

Three things you won't get elsewhere — together, in one tool:

1. **YAML when it's simple, code when it's not.** Declarative `evals.yaml` covers the no-code cases; pytest-style `@suite` decorators cover the rest — loops, fixtures, debugger breakpoints, IDE autocomplete. Promptfoo makes you generate YAML from Python scripts once your suite grows past a few dozen tests. Here Python is native, not a code-generation round trip.
2. **Local by design.** One SQLite file. No account, no API key for the framework, no cloud to trust. LangSmith and DeepEval's flagship features push your prompts and outputs to their servers — disqualifying for regulated industries, IP-sensitive work, or anyone who reads their procurement policy.
3. **No per-run judge tax.** Most assertions are deterministic: semantic similarity, schema, JSON, regex, grounding, tool-use. CI runs cost $0. RAGAS's headline metrics (faithfulness, answer relevancy, context precision) all need judge-model calls — every run costs tokens, adds latency, and drifts when the judge model updates. We treat LLM-as-judge as an opt-in, not a default.

| | Promptfoo | RAGAS | LangSmith | DeepEval | **promptry** |
|---|---|---|---|---|---|
| **Config** | YAML | Python metrics | SaaS UI | Python | **YAML or Python decorators** |
| **Data location** | Local | Local | **Their cloud** | Local + push | **Local SQLite** |
| **Account required** | No | No | **Yes** | No (for OSS) | **No, ever** |
| **CI cost per run** | Mixed | **Per-judge-call** | Trace volume | **Per-judge-call** | **$0 (deterministic)** |
| **Prompt versioning** | Manual + git | None | Prompt Hub | None | **Automatic content-hash** |
| **Live prompt editing** | None | None | Prompt Hub (cloud) | None | **Dashboard, no redeploy** |
| **Drift detection** | None | None | Dashboards only | None | **Mann-Whitney U + p-values** |
| **Cost budgets + alerts** | None | None | Usage charts only | None | **Daily/monthly caps** |
| **MCP server** | Plugin | None | None | Partial | **Native** |
| **Commercial tier** | Promptfoo Enterprise | None | LangSmith (SaaS) | Confident AI | **None planned** |

## GitHub Action

Run eval suites in CI with one line. On pull requests it posts (or updates) a single comment summarizing the eval: overall score, pass/fail counts, and any regressed tests vs. the previous run. [View on Marketplace.](https://github.com/marketplace/actions/promptry-eval)

```yaml
# .github/workflows/eval.yml
name: Eval
on: [push, pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write  # required for PR comments
    steps:
      - uses: actions/checkout@v4
      - uses: bihanikeshav/promptry@v1
        with:
          suite: rag-regression
          module: evals
          compare: prod  # optional — compare against baseline
```

Example PR comment on a regression:

```markdown
## promptry eval: rag-regression

| | Current | Baseline | Delta |
|---|---|---|---|
| Overall score | 0.891 | 0.910 | -0.019 |
| Passed | 8/10 | 9/10 | -1 |
| Status | REGRESSED | PASS | |

**Regressions:**
- `test_photosynthesis_answer`: semantic 0.89 -> 0.72 (-0.17)
- `test_schema_validation`: passed -> **failed**

_Generated by [promptry](https://github.com/bihanikeshav/promptry)_
```

Subsequent pushes edit the same comment instead of spamming new ones.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `suite` | Yes | | Eval suite name |
| `module` | Yes | | Python module containing the suite |
| `compare` | No | | Baseline tag to compare against |
| `python-version` | No | `3.12` | Python version |
| `extras` | No | `semantic` | pip extras to install |
| `pr-comment` | No | `true` | Post/update a PR comment with results |
| `github-token` | No | `${{ github.token }}` | Token used to post PR comments |

## MCP server

```bash
claude mcp add promptry -- promptry mcp    # Claude Code
```

Works with Claude Desktop, Cursor, Windsurf, VS Code. Agents don't just run evals — they create them: `list_suite_candidates` surfaces cases from golden examples or positively-rated production logs, `create_eval_suite` writes them into a runnable `evals.yaml`, and `run_eval` executes it. Everything the agent creates appears (and stays editable) on the dashboard. See [full setup](docs/guide.md#mcp-server-llm-agent-integration).

## Documentation

The [full guide](docs/guide.md) covers all assertions, cost tracking, model comparison, safety templates, notifications, storage modes, JS client, CLI reference, MCP setup, and config options.

## Scope

Promptry is local-first by design. If you need a hosted, always-on observability product for production traffic with team seats and SSO, use LangSmith or Arize — different product category. Promptry runs against one SQLite file on your machine: wire it into CI so a bad prompt change never reaches production, manage your live prompts from the dashboard, and keep a per-call ledger of cost and traces without sending anything to a vendor.

### Maturity

promptry is Beta: the core is stable and tested, the surrounding surfaces are
usable but still moving. Honest status per component:

| Component | Status | Notes |
| --- | --- | --- |
| Prompt registry + versioning | **Stable** | SQLite-backed, covered by tests |
| Eval suites (YAML + Python) + assertions | **Stable** | deterministic assertions are the core |
| Drift detection | **Stable** | statistical tests over recorded runs |
| Cost tracking + bundled price snapshot | **Stable** | reroute-aware pricing |
| CLI + GitHub Action | **Stable** | wire into CI today |
| Dashboard (local web UI) | **Beta** | broad feature set, UI still evolving |
| Cache optimization (reorder / shorten) | **Stable** | analysis only, advisory |
| Cache optimization (consolidate Apply) | **Beta** | writes a prompt version, gated behind `[dashboard] cms` |
| MCP server | **Beta** | create + run evals from an LLM agent |
| JS client (`promptry-js`) | **Beta** | OpenAI drop-in + call traces, at parity with Python capture; HTTP telemetry, no local SQLite |
| Postgres scale-tier (`[postgres]`) | **Alpha** | shared PG backend, opt-in; SQLite stays the tested default |
| VS Code extension | **Experimental** | thin wrapper over the CLI |
| Semantic assertions / RAG (`[semantic]`) | **Beta** | optional, needs the extra |
| Agent trajectory analysis, LLM root-cause | **Experimental** | on the roadmap |

Reroute-aware pricing ships in 0.10: when a provider retires a slug and silently
serves a pricier model (e.g. xAI's 2026-05-15 grok-4-fast → grok-4.3 at ~6x), the
cost engine prices by the model that actually billed, not the requested name.
Prices are a bundled snapshot you refresh on your terms — `promptry prices` lists
them, `promptry prices --refresh` pulls a static published feed (or your local
litellm) into `~/.promptry/prices.json`, and `promptry prices --check` flags
ledger models with no rate. No hosted service, no daily phone-home: nothing leaves
your machine unless you ask it to.

On the roadmap: agent trajectory analysis and LLM-powered root cause.

## License

MIT
