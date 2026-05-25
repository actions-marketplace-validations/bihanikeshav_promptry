"""Project-level config in ``.promptry/config.toml`` — committed to the repo
so a team's model list, judge settings, dashboard prefs, and pricing
overrides travel through git. API keys are NEVER stored here; they live in
env vars (read by litellm) and we only report which providers have a key.

Layout::

    .promptry/config.toml
      [dashboard]
      default_days = 14

      [judge]
      model = "gpt-4o-mini"

      [[models]]
      id = "gpt-4o-mini"
      provider = "openai"
      label = "GPT-4o mini"

      [pricing.my-custom-model]
      in = 1.0
      cached = 0.5
      cache_write = 1.0
      out = 2.0
"""
from __future__ import annotations

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
    """Project config path: ./.promptry/config.toml (cwd), committable."""
    return Path.cwd() / ".promptry" / "config.toml"


def load_project_config() -> dict:
    """Load .promptry/config.toml (or ~/.promptry/config.toml as fallback).
    Returns a dict with sensible defaults filled in."""
    data: dict = {}
    for p in (config_path(), Path.home() / ".promptry" / "config.toml"):
        if p.is_file():
            try:
                with open(p, "rb") as f:
                    data = tomllib.load(f)
                break
            except Exception:
                pass
    data.setdefault("dashboard", {}).setdefault("default_days", 14)
    data.setdefault("judge", {})
    if not data.get("models"):
        data["models"] = list(_DEFAULT_MODELS)
    data.setdefault("pricing", {})
    return data


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
            lines.append(f'{k} = "{_toml_escape(v)}"')
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


def key_status() -> dict[str, bool]:
    """Which provider keys are present in the environment (True/False).
    Never returns the key values themselves."""
    return {prov: bool(os.environ.get(env)) for prov, env in PROVIDER_ENV.items()}


def apply_pricing_overrides() -> int:
    """Merge any [pricing.*] overrides from config into promptry.pricing.RATES.
    Returns the number of models overridden."""
    data = load_project_config()
    overrides = data.get("pricing", {})
    if not overrides:
        return 0
    try:
        from promptry import pricing
        for name, rates in overrides.items():
            if isinstance(rates, dict):
                pricing.RATES[name] = {
                    "in": float(rates.get("in", 0)),
                    "cached": float(rates.get("cached", rates.get("in", 0) * 0.5)),
                    "cache_write": float(rates.get("cache_write", rates.get("in", 0))),
                    "out": float(rates.get("out", 0)),
                }
        return len(overrides)
    except Exception:
        return 0
