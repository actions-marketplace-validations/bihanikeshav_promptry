# Changelog

## 1.0.7 (2026-07-31)

- **Pricing catalog = LiteLLM only.** Removed the hand-maintained ~24-model
  rate table. Rates come from LiteLLM’s `model_cost` map via:
  - packaged `promptry/data/prices.json` (CI-generated snapshot),
  - published `prices.json` on GitHub (dashboard 24h pull / `prices --refresh`),
  - or live `promptry prices --litellm` when litellm is installed.
- Provider-prefixed slugs also register bare aliases (`azure_ai/grok-4` → `grok-4`).
- **Reroutes** remain a small optional xAI map in code — **not** from LiteLLM.

## 1.0.6 (2026-07-31)

- **Docs: shared dashboard secret** — README + guide + INTEGRATION document the
  single-API-key auth model, session TTL (7d), vault distribution, and rotation.
- **Price feed auto-refresh** — dashboard pulls the published `prices.json`
  feed on startup and every 24h (opt-out: `PROMPTRY_PRICES_AUTO_REFRESH=0`).
  CLI remains opt-in (`promptry prices --refresh`). Repo ships `prices.json`;
  daily GitHub Action refreshes it from litellm when available.
- `GET /api/cost/prices-meta`, `POST /api/cost/refresh-rates?source=feed|litellm|both`.

## 1.0.5 (2026-07-31)

Dashboard hardening for reverse-proxied / public hosts.

- **Feedback curl host** — empty-state copy uses the page origin
  (`window.location.origin`) instead of hard-coded `localhost:8420`.
- **Optional auth** — set `PROMPTRY_AUTH_TOKEN` (or `PROMPTRY_DASHBOARD_TOKEN`)
  to lock every `/api/*` route except health + auth. Browser login issues an
  HttpOnly signed session cookie; machine clients send
  `Authorization: Bearer <token>`. Unset = open (local-only default).
- CLI prints whether auth is on at dashboard start.

## 1.0.4 (2026-07-09)

Cache optimization — a dashboard page (`/cache`) and CLI (`promptry cache`) with
three modes for cutting input-token cost, plus a prompt-CMS gate.

### Cache optimization

- **Reorder inputs** — per-prompt prefix-cache readiness. Prefix caching reuses
  the request up to the first interpolated input and only activates above
  ~1024 tokens, so the lever is ordering: static instructions first,
  `{{inputs}}` last. Shows each prompt's cacheable prefix now vs. if inputs
  moved to the end, judged against real telemetry, and honestly reports
  "too small to cache" below the floor. (`prompt_diff.prefix_cache_analysis`,
  `GET /api/prompts[/{name}]/cache-analysis`.)
- **Consolidate** — near-duplicate forks shown side-by-side (both prompts equal
  weight, local no-refetch ↔ swap), with a CMS-gated Apply to make one adopt the
  other's wording. Fixes the previous cross-prompt "% shared" framing.
- **Shorten** — flags redundant/filler wording in a prompt's static text and
  measures the tokens it costs: duplicate/near-duplicate sentences, a curated
  filler lexicon, over-repeated format keywords, and (with `[semantic]`)
  embedding-based semantic redundancy. Each redundancy pair is highlighted
  inline in its own colour against its original; filler is advisory. Flag-only —
  no rewriting, no model calls. (`prompt_diff.shorten_analysis`,
  `GET /api/prompts[/{name}]/shorten-analysis`.)
- **CLI**: `promptry cache`, `promptry cache <name>`, `promptry cache --shorten`,
  all with `--json` for CI.

### Prompt CMS gate

- Prompt-write actions in the dashboard (edit content, promote to env, apply a
  consolidation) are gated behind `[dashboard] cms = true` in `promptry.toml`,
  **off by default** — editing from the dashboard only takes effect if the app
  fetches its prompts from promptry (`render_prompt`/`get_prompt`). When off, the
  buttons are disabled with a hint and the API returns 403.

## 1.0.3 (2026-07-08)

Packaging and honesty pass — no functional changes to the core.

### Lighter install

- **Core is now small.** `pip install promptry` no longer pulls
  `sentence-transformers`, `chromadb`, `litellm`, `openai`, or `anthropic`.
  Core keeps the CLI, prompt registry, deterministic assertions, drift, cost
  tracking from the bundled price snapshot, the local **dashboard**, and the
  MCP server.
- **New extras.** `promptry[semantic]` (sentence-transformers + chromadb, for
  `assert_semantic` / embedding distance / RAG context / clustering),
  `promptry[llm]` (litellm + openai + anthropic, for real completions /
  `assert_llm` / live price refresh), and `promptry[full]` for everything (the
  pre-`1.0.3` behavior). The empty placeholder extras were removed.
- **Actionable errors.** Calling a feature whose extra isn't installed now
  raises an `ImportError` naming the exact `pip install 'promptry[...]'` to run,
  instead of a bare `ModuleNotFoundError`.

### Honesty

- `__version__` is now sourced from the installed package metadata, so it can
  never drift from what pip actually installed (was a hand-maintained literal).
- Docs site and demo dashboard read the live version from the GitHub releases
  API; static markup is only a fallback. GitHub Action examples pin the
  floating `@v1` major tag.
- Trove classifier is now `4 - Beta` (was `3 - Alpha`); the README carries a
  per-component stable/beta/experimental maturity table instead of claiming
  everything is shipped.

## 1.0.2 (2026-07-08)

Fixes the lint CI that failed on `1.0.1`: resolves ruff findings across the
codebase (drop dead imports in `embeddings`, `assertions.text`, and the MCP
server; hoist the MCP server's `pathlib` import) and ignores `F401` in package
`__init__.py` files, whose imports are intentional public-API re-exports. No
runtime changes from `1.0.1`.

## 1.0.1 (2026-07-08)

First feature release since the `0.10.x` line. Versioned `1.0.1` (not `0.11.0`):
`1.0.0` was accidentally published then yanked, and because pip sorts `1.0.0`
above any `0.x`, only a version greater than `1.0.0` pulls users who installed
that build forward on `pip install -U`. `1.0.1` supersedes the yanked `1.0.0`
and the `0.10.x` line.

### Evals

- **Suite builder** (`promptry.suite_builder`) — assemble and persist
  declarative `evals.yaml` suites programmatically: `build_suite_dict` +
  `write_yaml_suite` (the single write path shared by the dashboard, the MCP
  server, and `promptry new suite`), `read_yaml_suite` for edit round-trips,
  and `suite_candidates` to source ready-to-edit cases from golden examples or
  positively-rated invocations. A case's retrieved `context` is emitted as a
  `grounded` assertion so it is actually exercised at run time.

### Dashboard

- **In-UI suite creator** (`/suites/new`, opened by the Evals page's
  **New suite** button — suite creation lives on the Evals page, not a
  separate nav item) — build an eval suite from three sources: manual cases,
  golden examples, or positive-feedback logs. RAG cases carry question /
  retrieved context / expected response, with a from-logs button that
  auto-fills context from recorded `track_context` data
  (`GET /api/prompts/{name}/recorded-context`).
- **Suite editing** — per-suite **Edit** button on the Evals page
  (`/suites/new?edit=<name>`). Any YAML-declared suite is editable regardless
  of whether it was created in the dashboard, via MCP, or by
  `promptry new suite`; Python-defined suites are shown read-only. New
  endpoints: `POST /api/suites`, `GET /api/suite-candidates`,
  `GET /api/suites/{name}/definition`.
- **Cache optimization page** (`/cache`, formerly "Duplicates") —
  near-duplicate prompt pairs with a cross-prompt diff plus a prompt-prefix
  cache analysis: shared-prefix ratio and a recommendation to restructure
  static text to the front to improve prefix-cache hit rate. Backed by
  `GET /api/prompts/diff2?a=&b=` and `promptry.prompt_diff`; CLI equivalent
  `promptry prompt diff2 <a> <b>`.

### MCP

- **Agents can create evals, not just run them** — new `create_eval_suite`
  tool writes a runnable `evals.yaml` suite (cases as
  `{input, context?, expect: [{type, value}]}` with assertion types contains /
  not_contains / regex / exact / semantic / grounded / llm; a case's context
  auto-becomes a grounded assertion) that immediately appears, and is
  editable, on the dashboard. New `list_suite_candidates` tool sources cases
  from golden examples or positive-feedback logs.
- `list_suites`, `run_eval`, and `check_drift` now default `module="evals"`
  and discover YAML suites (auto `evals.yaml` / `promptry.yaml`), not just
  Python modules.

### Config

- **`[keys]` provider-key env-var aliases** in `promptry.toml` — point a
  provider at a non-standard env-var name
  (`[keys]` `openai = "MY_OPENAI_KEY"`); the aliased value is bridged to the
  canonical variable at call time. Only the variable NAME is stored — never
  the secret. The dashboard's Settings page auto-detects keys and lets you
  click an undetected provider to enter its variable name.

## 0.10.1 (2026-07-08)

Version-correction release. An artifact was accidentally published to PyPI as
`1.0.0`; that release has been yanked. `0.10.1` is the intended continuation of
the `0.10.x` line and supersedes it. No functional changes from `0.10.0` — the
API is still pre-1.0 and may shift before a stable 1.0.

## 0.10.0 (2026-07-08)

First broad public release. The library, CLI, dashboard, GitHub Action, and MCP
server work end to end around a single local SQLite store. Numbered 0.10.0, not
1.0 — the API may still shift before a stable 1.0.

### Prompts

- **Live prompt CMS** — `render_prompt()` / `seed_prompt()` serve
  dashboard-edited templates with no redeploy; edit, diff, version, and
  promote prompts from the UI.
- **`{{name}}` templating** — value-driven substitution recognizes `{{name}}`,
  `{name}`, `${name}`, and `$name` but only substitutes the variables you
  actually pass, so JSON braces and literal `$` are never touched. `$`-style is
  normalized to `{{}}` on save/seed.
- **Environment promotion** — dev/staging/prod tags gate which version
  `render_prompt(env=…)` serves. Prompt linting on save; template variables
  surfaced as first-class metadata.
- **Semantic prompt search + near-duplicate detection** (embeddings, with a
  lexical fallback).

### Evals

- **Eval↔prompt linkage**, **regression bisect** across runs, and **online
  drift** on production telemetry (Mann-Whitney with an effect-size floor).
- **Judge-cost attribution** per run; **eval-from-trace** — promote a captured
  invocation into a per-prompt golden set and re-run it against any model.
- **Latency SLO gates** — `[slo]` budgets fail CI independently of the score.

### Cost & traces

- Per-call **invocation ledger** (`track_invocation`) with module→prompt→call
  cost drill-down and a template-vs-payload split; **cost budgets** with breach
  tracking; pricing auto-refresh + uncosted-model coverage report.
- Opt-in **trace capture** + end-user **feedback ingest** by `request_id`;
  **PII/secret scanning** of captured traces (regex tripwire, masked findings).
- **Reroute-aware pricing** — when a provider retires a slug and silently serves
  a pricier model (e.g. xAI's 2026-05-15 grok-4*-fast → grok-4.3 at ~6x), the
  cost engine prices by the model that actually billed (`served_model` + call
  date), instead of undercounting at the requested slug's old rate.
- **PII-safe trace viewer** — captured request/response text is redacted in
  place before the dashboard serves it, so the viewer can't re-expose what the
  scanner flagged.
- **Configurable capture/judge limits** — trace capture length defaults to 50k
  chars and is tunable via `[capture] max_chars` (`0` = unlimited); the
  regression-explanation judge prompt cap is tunable via `[judge]
  max_prompt_chars`.

### Dashboard & config

- Rebuilt React dashboard (Overview, Evals, Prompts, Cost, Models, Playground,
  Settings) with live `{{var}}` highlighting and a model-comparison playground.
- **`.promptry/config.toml`** (committed) holds models, judge, dashboard prefs,
  and pricing overrides; API keys stay in env.
- A clickable **live demo** of the dashboard and an **agent integration guide**
  (`docs/INTEGRATION.md`).
- **Feedback view** — satisfaction rate, per-prompt breakdown, and a daily
  positive-rate sparkline over end-user feedback, each row linking back to the
  invocation that produced the response.
- **Server-side paging + sorting** for the invocation/cost lists (limit/offset +
  sort column/direction), plus a suite search box on the Evals list.

### Agents & capture


- **Agent trajectory model** (`promptry.trajectory`) — a `Trajectory`
  dataclass with `.from_openai` / `.from_anthropic` / `.from_dicts`
  constructors, plus `analyze_trajectory` for structural stats
  (step count, tool counts, loops detected, token/duration totals)
  and four new assertions: `assert_trajectory_max_steps`,
  `assert_no_redundant_tool_calls`, `assert_tool_input_matches`,
  `assert_final_answer_present`.
- **Trajectory diff** — `diff_trajectories(baseline, candidate)`
  returns a `TrajectoryDiff` with added/removed/reordered tool
  calls and step/duration/token deltas.
- **Production capture + replay** (`promptry.capture`) —
  `CaptureRecorder` writes append-only JSONL at
  `.promptry/captures/<task>.jsonl`. `default_recorder(task)` is
  drop-in prod capture when `PROMPTRY_CAPTURE=1` is set.
  `replay_captures(caps, pipeline, compare=...)` runs captured
  inputs through the candidate pipeline and reports drift vs
  the baseline output. `redact_sensitive()` scrub helper for
  metadata at capture time.
- **Failure clustering** (`promptry.clustering`) —
  `cluster_failures(suite, days=7)` groups failing assertions by
  signature so recurring patterns are visible. Semantic mode
  (sentence-transformers) with string fallback.
- **Garak result importer** (`promptry.garak`) —
  `promptry garak import REPORT.jsonl` reads a NVIDIA garak
  JSONL report and writes each (probe, detector) pair into
  promptry's own storage. Garak runs show up as normal suites,
  drift/history/comparison work on them for free. No runtime
  dep on garak — just parses its output file.

### Bug fixes

- `python -m promptry.cli` silently exited with rc=0 — missing
  `if __name__ == "__main__"` guard. Added, plus a new `__main__.py`
  so `python -m promptry` also works.
- `cluster_failures` raised `TypeError` on naive-vs-aware datetime
  comparison. Parser now forces UTC.
- Concurrent `CaptureRecorder` instances writing to the same file
  raced and lost entries. Now a path-keyed global lock registry
  serializes writes across instances.
- Dashboard CLI advertised a phantom hosted URL
  `promptry.meownikov.xyz/dashboard?port=N` that doesn't exist.
  Removed — dashboard is local-only, matching the "no cloud"
  positioning.
- Dashboard auto-open raced uvicorn's port bind and often opened
  the browser before the server was ready, showing
  `ERR_CONNECTION_REFUSED`. Now the browser open is deferred
  until `/api/health` responds.
- When the dashboard port was already in use, uvicorn silently
  exited after printing startup-looking messages. Now the CLI
  probes the port first and exits with a clear error.
- pytest `TestResult` collection warning spammed every test run.
  Fixed with `__test__ = False`.

### Added

- **Single install** — `pip install promptry` now pulls everything (semantic
  search, dashboard, judge providers). The old extras (`promptry[semantic]`,
  `promptry[dashboard]`, `promptry[openai]`, etc.) still install but are now
  empty groups, kept only for compatibility.
- **YAML declarative suites** — write suites as `evals.yaml` instead of (or
  alongside) Python `@suite` files. `--module` accepts an explicit
  `.yaml`/`.yml` path, or auto-discovers `evals.yaml`/`evals.yml`/
  `promptry.yaml`/`promptry.yml` when left at its default (`evals`) and no
  `evals.py` is present.
- **`promptry new suite`** — a wizard that scaffolds a suite interactively or
  fully via flags (`--name`, `--yaml`/`--python`, `--pipeline` or
  `--model`/`--prompt`, repeatable `--case`), writing `evals.yaml` or
  `evals.py` and printing the exact `promptry run` command to try it.
- **`--format json|junit`** on `run`, `compare`, and `drift`, plus `--output`
  to write the report (HTML/plain text for `table`, JSON or JUnit XML for the
  other formats) straight to a file.
- **Seven new CLI commands**: `promptry lint` (prompt-template footgun
  checker, CI-gates on error-level findings), `promptry prompt search`
  (semantic prompt search), `promptry prompt duplicates` (near-duplicate
  prompt detection), `promptry cluster` (group recent failed assertions into
  patterns), `promptry scan` (PII/secret tripwire over captured invocations,
  `--fail-on-hit` for CI), `promptry replay` (replay captured production
  inputs through the current pipeline and diff the output), and
  `promptry golden` (re-run a prompt's golden examples through a model and
  score drift against the recorded reference).
- **Judge auto-configuration** — `get_judge()` now falls back to a judge
  auto-built from `[judge] model` in `promptry.toml` when no explicit
  `set_judge()` callable is registered.
- **New deterministic assertions**: `assert_exact`, `assert_levenshtein`,
  `assert_rouge_l`, `assert_embedding_distance`.
- **JS SDK tracking expansion + wire schema** —
  `docs/wire-schema/events.schema.json` is now the single source of truth for
  the batch payload that the Python `RemoteStorage` backend and the
  `promptry-js` client both emit.
- **Dashboard onboarding empty states** guide new projects that have no
  suites or evals yet toward their first run.
- **Shell completions** (`promptry --install-completion`).
- **`promptry doctor` exit codes** — exits 1 when any check fails, so it can
  gate CI/setup scripts instead of only printing warnings.

### Changed

- **Unified config** — team/project settings that used to live in a separate
  `.promptry/config.toml` now belong in the canonical `promptry.toml`; the
  legacy file is still read for back-compat and merged in, but `promptry.toml`
  wins on conflicts.
- **Invocations schema migration + SQL aggregation** — invocation metrics
  promoted to typed columns with aggregation pushed into SQL, instead of
  pulling rows into Python to sum.
- **Safety template catalog** extracted from inline Python into a packaged
  TOML file (`promptry/data/safety_templates.toml`).
- **`promptry.assertions`** converted from a single module into a package
  (`judge`, `json_utils`, `text`, `tools`, `conversation` submodules);
  existing `from promptry.assertions import ...` usage is unaffected.
- **Dashboard server split into routers**, one per API domain, replacing the
  single monolithic `server.py` route file.
- **VS Code extension** consumes `--format json` for run results and adds a
  `promptry.module` setting; bumped to 0.10.0 to match the core package.

### Deprecated

- Optional-dependency **extras** (`promptry[semantic]`, `promptry[dashboard]`,
  `promptry[openai]`, `promptry[litellm]`, `promptry[anthropic]`, etc.) — kept
  as empty groups for compatibility; everything they used to gate now
  installs by default.
- **`--local`** dashboard flag — no longer needed, kept only so existing
  invocations don't break.
- **`.promptry/config.toml`** legacy path — still honored, but new settings
  should go in `promptry.toml`.

## 0.8.0

### New features

- **Continuous sampling mode** (`promptry sample SUITE --every N`) — re-run a
  suite on a fixed time interval, the cron-like companion to `watch`. Useful
  for long-running dev sessions and intermittent-regression hunting.
- **LLM-powered regression explanations** (`promptry run ... --explain`) —
  when a suite regresses against its baseline, the existing deterministic
  hints (prompt changed, model changed, retrieval drift) get an optional
  natural-language companion. Opt-in, requires `set_judge()`. Default CI
  path remains fully deterministic with zero API calls.
- **Dataset generation from spec** (`promptry dataset generate SPEC -o OUT`) —
  given a short YAML/TOML description of the eval dataset you want, synthesize
  a ready-to-run `@suite`-decorated Python file with N test cases. Opt-in,
  requires `set_judge()`.
- **Safety template expansion** — the built-in corpus grew from 25 to 65
  adversarial attack templates across the same 6 categories (prompt
  injection, jailbreak, PII leakage, hallucination, context boundary,
  encoding).

### Bug fixes

- `reset_storage()` now also resets the prompt registry. Previously the
  registry cached a reference to the closed storage instance, causing
  `sqlite3.ProgrammingError: Cannot operate on a closed database` on any
  subsequent `track()` call. Mostly affected users rotating the DB path
  in test harnesses.

### Testing

- New `integration_tests/` harness exercises promptry end-to-end against a
  real RAG pipeline (local Ollama + in-process ChromaDB). Not part of the
  default `pytest` run — invoke explicitly via `pytest integration_tests/`.
  Requires `pip install promptry[integration]` plus a running Ollama.

### Packaging

- New optional-dependency groups:
  - `promptry[dataset-gen]` — adds `pyyaml` for `.yaml` spec files.
  - `promptry[integration]` — adds `chromadb` and `pyyaml` for the
    integration-test harness.

## 0.7.0

- Multi-turn conversation evals
- `promptry watch` (file-change-triggered re-runs)
- Eval diff view in dashboard
- GitHub PR bot (single-comment updates)
- Prompt cache awareness across OpenAI, Anthropic, Gemini, Grok
- Tool-use assertions

## 0.6.0

- Initial public release
