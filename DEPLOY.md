# Deploying promptry

promptry is **zero-infra**: one process over a single SQLite file. No Postgres,
Redis, or ClickHouse. Two ways to run it.

## Docker (recommended)

```bash
docker compose up          # builds + starts the dashboard
# open http://localhost:8420
```

Or without compose:

```bash
docker build -t promptry .
docker run -p 8420:8420 -v promptry-data:/data promptry
```

All state (the SQLite DB, cached model prices, session secret) lives in the
`/data` volume and survives restarts.

## pip

```bash
pip install "promptry[llm]"
promptry dashboard          # serves on http://localhost:8420
```

## Feed it real data (30 seconds)

Change one import and every OpenAI call is tracked — cost, tokens, prompt, and
the prefix-cache signal — no other code changes:

```python
# from openai import OpenAI
from promptry.openai import OpenAI

client = OpenAI()
client.chat.completions.create(model="gpt-4o", messages=[...])
```

Refresh the dashboard and your calls, their cost, and the **prefix-cache
optimizer's projected savings on your own prompts** show up under Cost / Prompts.

## What's on by default

Everything that matters runs with **zero config**: the prompt registry, eval
suites, the cost dashboard + price DB, capture, and the cache optimizer. The
dashboard is a **simple single-user** app by default.

## Optional hardening (all off by default)

Set these only when you need them — e.g. when exposing the dashboard beyond
localhost.

| Env var | Effect |
|---|---|
| `PROMPTRY_AUTH_TOKEN` | Require a shared secret (Bearer / login) for `/api/*` |
| `PROMPTRY_ALLOWED_HOSTS` | Extra allowed `Host` values (DNS-rebind guard) |
| `PROMPTRY_CORS_ORIGINS` | Explicit cross-origin allowlist (default: same-origin) |
| `PROMPTRY_CAPTURE_REDACT_PII` | Scrub secrets/PII from captured text before storing |
| `PROMPTRY_*_RETENTION_DAYS` | Age out captured text / invocations / audit rows |
| `PROMPTRY_ENFORCE_BUDGETS` | Turn tracked budgets into a hard spend ceiling |
| `PROMPTRY_DB_KEY` | Encrypt the DB at rest (needs `promptry[encryption]`) |

**Team access (alpha):** multi-user accounts, roles (viewer / editor / admin),
audit log, and OIDC/SSO are opt-in. Enable from **Settings → Team & access**, or
by creating the first user via the API. Until then the dashboard stays simple.

## Scaling past one node

SQLite is single-writer — fine for a single dashboard/app instance even at high
traffic (capture is async + sampled). For **many** app instances writing at
once, point them at one promptry ingest endpoint (remote storage mode) rather
than sharing a file. A Postgres backend for that tier is on the roadmap; you do
**not** need Redis.
