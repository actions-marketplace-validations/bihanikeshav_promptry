# Changelog

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
