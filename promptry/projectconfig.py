"""Project-level / team config — the model list, judge settings, dashboard
prefs, and pricing overrides that travel through git so a team shares one
setup. API keys are NEVER stored here; they live in env vars (read by litellm)
and we only report which providers have a key.

These sections used to live in a *separate* ``.promptry/config.toml`` file,
disjoint from the runtime ``promptry.toml`` that :mod:`promptry.config` reads.
As of the config unification they belong in the one canonical ``promptry.toml``
alongside ``[storage] [tracking] [model] [monitor]``. ``load_project_config()``
merges, in increasing order of precedence:

  1. ``~/.promptry/config.toml``   — user-level fallback
  2. ``./.promptry/config.toml``   — legacy project file (still honored for
     back-compat; prefer moving these sections into ``promptry.toml``)
  3. ``./promptry.toml``           — canonical project file (wins on conflicts)

Layout (all in ``promptry.toml``)::

      [dashboard]
      default_days = 14

      [judge]
      model = "gpt-4o-mini"
      max_prompt_chars = 8000  # cap judge-prompt size (token-spend guard); 0 = off

      [slo]                  # CI fails the run if a budget is breached
      max_latency_ms = 8000  # no single test slower than this
      p95_latency_ms = 5000  # 95th-percentile test latency

      [[models]]
      id = "gpt-4o-mini"
      provider = "openai"
      label = "GPT-4o mini"

      [pricing.my-custom-model]
      in = 1.0
      cached = 0.5
      cache_write = 1.0
      out = 2.0

The return value is a plain dict (unchanged shape). The result is memoized and
invalidated automatically when any source file's mtime changes; call
:func:`reset_project_config` to force a reload (mirrors ``reset_config``).
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# provider -> the env var that holds its key (for status display only).
PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}

_DEFAULT_MODELS = [
    {"id": "gpt-4o-mini", "provider": "openai", "label": "GPT-4o mini"},
    {"id": "gpt-4o", "provider": "openai", "label": "GPT-4o"},
    {"id": "claude-haiku-4-5", "provider": "anthropic", "label": "Claude Haiku 4.5"},
]


def config_path() -> Path:
    """Legacy project config path: ./.promptry/config.toml (cwd).

    This is where the dashboard's Settings page writes team config for
    back-compat. Reads unify this file with the canonical ``promptry.toml``
    (see :func:`load_project_config`); on conflicts ``promptry.toml`` wins.
    """
    return Path.cwd() / ".promptry" / "config.toml"


def _config_sources() -> list[Path]:
    """Source files in *increasing* order of precedence (later wins)."""
    return [
        Path.home() / ".promptry" / "config.toml",  # user-level fallback
        Path.cwd() / ".promptry" / "config.toml",   # legacy project file
        Path.cwd() / "promptry.toml",               # canonical project file
    ]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (override wins). Nested
    tables are merged key-by-key; scalars and lists are replaced wholesale."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _sources_signature() -> tuple:
    """A cheap fingerprint of the source files (path + mtime) so the cache can
    self-invalidate when any file changes without an explicit reset."""
    sig: list = []
    for p in _config_sources():
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            pass  # file absent -> contributes nothing
    return tuple(sig)


def _load_project_config() -> dict:
    data: dict = {}
    for p in _config_sources():
        if p.is_file():
            try:
                with open(p, "rb") as f:
                    parsed = tomllib.load(f)
                data = _deep_merge(data, parsed)
            except Exception:
                pass
    data.setdefault("dashboard", {}).setdefault("default_days", 14)
    data.setdefault("judge", {})
    if not data.get("models"):
        data["models"] = list(_DEFAULT_MODELS)
    data.setdefault("pricing", {})
    data.setdefault("slo", {})
    return data


# Memoized view. Keyed on the source-file signature so a file edit (e.g. from
# the dashboard, or a hand edit while a long-running server is up) is picked up
# without a restart, while a hot loop (per-assertion judge costing) hits cache.
_project_cache: dict | None = None
_project_sig: tuple | None = None


def load_project_config() -> dict:
    """Load the unified project config as a dict (cached).

    Merges ``~/.promptry/config.toml`` (fallback), the legacy
    ``.promptry/config.toml``, and the canonical ``promptry.toml`` — the latter
    wins on conflicts. Sensible defaults are filled in. The result is memoized
    and auto-invalidated on source-file mtime changes; use
    :func:`reset_project_config` to force a reload.
    """
    global _project_cache, _project_sig
    sig = _sources_signature()
    if _project_cache is None or _project_sig != sig:
        _project_cache = _load_project_config()
        _project_sig = sig
    # Return a deep copy so callers (e.g. the dashboard's update path) can
    # mutate the result without poisoning the shared cache.
    return copy.deepcopy(_project_cache)


def cms_enabled() -> bool:
    """Whether the live prompt CMS is turned on (``[dashboard] cms = true``).

    Off by default: editing or promoting prompts from the dashboard only has an
    effect if the app actually fetches its prompts from promptry (render_prompt /
    get_prompt), so every prompt-write surface stays gated until the user opts in.
    """
    try:
        return bool(load_project_config().get("dashboard", {}).get("cms", False))
    except Exception:
        return False


def load_raw_config(path: Path | None = None) -> dict:
    """Load the raw, *unmerged* contents of a single config file — no merge
    with the other sources, no defaults filled in.

    This exists for the dashboard's settings-save path: it must mutate and
    rewrite only the file it's actually about to write (the legacy
    ``.promptry/config.toml``), never the merged view from
    :func:`load_project_config` — otherwise values sourced from
    ``~/.promptry/config.toml`` or the canonical ``promptry.toml`` get copied
    into the legacy file, and keys that also live in ``promptry.toml`` get
    silently shadowed on the next read.
    """
    p = path if path is not None else config_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def reset_project_config() -> None:
    """Drop the memoized project config (mirrors ``config.reset_config``)."""
    global _project_cache, _project_sig
    _project_cache = None
    _project_sig = None


def _toml_escape(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _dump_toml(data: dict) -> str:
    """Minimal TOML writer for our known schema (no external dep)."""
    lines: list[str] = []
    def _val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return '"' + _toml_escape(v) + '"'

    dash = data.get("dashboard", {})
    if dash:
        lines.append("[dashboard]")
        for k, v in dash.items():
            lines.append(f"{k} = {_val(v)}")
        lines.append("")
    judge = data.get("judge", {})
    if judge:
        lines.append("[judge]")
        for k, v in judge.items():
            lines.append(f"{k} = {_val(v)}")
        lines.append("")
    slo = data.get("slo", {})
    if slo:
        lines.append("[slo]")
        for k, v in slo.items():
            lines.append(f"{k} = {_val(v)}")
        lines.append("")
    for m in data.get("models", []):
        lines.append("[[models]]")
        lines.append(f'id = "{_toml_escape(m.get("id", ""))}"')
        if m.get("provider"):
            lines.append(f'provider = "{_toml_escape(m["provider"])}"')
        if m.get("label"):
            lines.append(f'label = "{_toml_escape(m["label"])}"')
        lines.append("")
    for name, rates in data.get("pricing", {}).items():
        lines.append(f'[pricing."{_toml_escape(name)}"]')
        for k, v in rates.items():
            lines.append(f"{k} = {v}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_project_config(data: dict) -> None:
    """Write .promptry/config.toml (creates the folder)."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_dump_toml(data), encoding="utf-8")
    reset_project_config()  # next read reflects the write


def key_env_names() -> dict[str, str]:
    """The env-var name each provider's key is read from — the standard name
    (OPENAI_API_KEY, …) unless the user aliased it in ``[keys]`` of the config,
    e.g. ``[keys] openai = "MY_OPENAI_KEY"`` for a non-standard variable name.
    Only the variable NAME is configurable; the secret value stays in the env."""
    overrides = (load_project_config().get("keys") or {})
    out: dict[str, str] = {}
    for prov, default_env in PROVIDER_ENV.items():
        alias = overrides.get(prov)
        out[prov] = alias if isinstance(alias, str) and alias.strip() else default_env
    return out


def key_status() -> dict[str, bool]:
    """Which provider keys are present in the environment (True/False),
    honoring any ``[keys]`` env-var-name aliases. Never returns the values."""
    return {prov: bool(os.environ.get(env)) for prov, env in key_env_names().items()}


def apply_key_aliases() -> int:
    """Bridge aliased provider keys to the standard env var litellm expects.

    litellm only reads the canonical names (OPENAI_API_KEY, …). When a user has
    their key under a non-standard variable and aliased it in ``[keys]``, copy
    that value into the canonical variable (without overwriting one already set)
    so provider calls actually succeed. Returns the number of aliases applied.
    Call before dispatching an LLM request."""
    applied = 0
    for prov, env in key_env_names().items():
        default_env = PROVIDER_ENV[prov]
        if env != default_env and os.environ.get(env) and not os.environ.get(default_env):
            os.environ[default_env] = os.environ[env]
            applied += 1
    return applied


def apply_pricing_overrides() -> int:
    """Merge any [pricing.*] overrides from config into promptry.pricing.RATES.
    Returns the number of models overridden."""
    data = load_project_config()
    overrides = data.get("pricing", {})
    if not overrides:
        return 0
    try:
        from promptry import pricing
        pricing.ensure_prices_loaded()
        for name, rates in overrides.items():
            if isinstance(rates, dict):
                pricing.RATES[name] = {
                    "in": float(rates.get("in", 0)),
                    "cached": float(rates.get("cached", rates.get("in", 0) * 0.5)),
                    "cache_write": float(rates.get("cache_write", rates.get("in", 0))),
                    "out": float(rates.get("out", 0)),
                }
        pricing._recompute_rate_indexes()
        return len(overrides)
    except Exception:
        return 0
