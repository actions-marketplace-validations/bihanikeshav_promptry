# Changelog

## 0.10.0 (2026-06-01)

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
