"""Prompt pricing with cache awareness across providers.

Rates are best-effort snapshots. Users should override via config or recompute
from provider invoices for finance purposes.
"""
from __future__ import annotations

# Per 1M tokens USD. Provider-model -> (input, cached_read, cache_write, output)
# Structured to capture each provider's caching economics:
#   - OpenAI: cached reads 50% off, no cache write premium
#   - Anthropic: cached reads 90% off (0.1x), cache writes 1.25x (5min) or 2x (1hr)
#   - Google Gemini: cached reads (Context Caching), rate by model
#   - xAI Grok: similar to OpenAI, cached_tokens reported
RATES = {
    # OpenAI
    "gpt-4o":           {"in": 2.50, "cached": 1.25, "cache_write": 2.50, "out": 10.00},
    "gpt-4o-mini":      {"in": 0.15, "cached": 0.075, "cache_write": 0.15, "out": 0.60},
    "gpt-4.1":          {"in": 2.00, "cached": 0.50, "cache_write": 2.00, "out": 8.00},

    # Anthropic (5-min TTL ephemeral cache assumed)
    "claude-opus-4":         {"in": 15.00, "cached": 1.50, "cache_write": 18.75, "out": 75.00},
    "claude-sonnet-4":       {"in": 3.00,  "cached": 0.30, "cache_write": 3.75,  "out": 15.00},
    "claude-haiku-4-5":      {"in": 0.80,  "cached": 0.08, "cache_write": 1.00,  "out": 4.00},

    # Google Gemini
    "gemini-2.5-pro":        {"in": 1.25, "cached": 0.31, "cache_write": 1.25, "out": 10.00},
    "gemini-2.5-flash":      {"in": 0.30, "cached": 0.075, "cache_write": 0.30, "out": 2.50},

    # xAI Grok (grok-2 / grok-3 era)
    "grok-2":                {"in": 2.00, "cached": 0.50, "cache_write": 2.00, "out": 10.00},
    "grok-3":                {"in": 3.00, "cached": 0.75, "cache_write": 3.00, "out": 15.00},
    # xAI Grok-4 family. grok-4-fast tiers are cheaper than grok-3; the
    # non-reasoning variant is what most production RAG pipelines use.
    # xAI deploys point-release suffixes (grok-4-1-fast-*) as the rates
    # don't change between them; covered by explicit entries below.
    "grok-4":                         {"in": 3.00, "cached": 0.75, "cache_write": 3.00, "out": 15.00},
    "grok-4-fast":                    {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-fast-non-reasoning":      {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-fast-reasoning":          {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-1":                       {"in": 3.00, "cached": 0.75, "cache_write": 3.00, "out": 15.00},
    "grok-4-1-fast":                  {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-1-fast-non-reasoning":    {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-1-fast-reasoning":        {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
}


def calculate_cost(
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Return USD cost for a call, or None if model isn't in the rate table.

    tokens_in includes cached_tokens (the total input). We subtract to get
    uncached input before applying the standard rate.
    """
    rates = _lookup_rates(model)
    if rates is None:
        return None

    uncached_in = max(0, tokens_in - cached_tokens - cache_write_tokens)
    cost = 0.0
    cost += (uncached_in / 1_000_000) * rates["in"]
    cost += (cached_tokens / 1_000_000) * rates["cached"]
    cost += (cache_write_tokens / 1_000_000) * rates["cache_write"]
    cost += (tokens_out / 1_000_000) * rates["out"]
    return round(cost, 6)


def _lookup_rates(model: str) -> dict | None:
    """Fuzzy lookup: 'gpt-4o-2024-11-20' -> 'gpt-4o' via prefix matching."""
    if not model:
        return None
    if model in RATES:
        return RATES[model]
    # Fallback: find best prefix match
    for key in sorted(RATES.keys(), key=len, reverse=True):
        if model.startswith(key):
            return RATES[key]
    return None


def is_known_model(model: str) -> bool:
    """True if we have a rate for this model (exact or prefix match).
    Unknown models silently cost $0, so callers can warn on False."""
    return _lookup_rates(model) is not None


def refresh_rates_from_litellm() -> int:
    """Pull current rates from litellm's model_cost map into RATES, so the
    hand-maintained snapshot stays current. Returns the number of models
    added/updated. No-op (returns 0) if litellm isn't installed.

    litellm stores per-token USD as input_cost_per_token / output_cost_per_token
    (and cache_*); we convert to per-1M and the {in,cached,cache_write,out}
    shape promptry uses.
    """
    try:
        import litellm  # noqa
        model_cost = getattr(litellm, "model_cost", None)
    except Exception:
        return 0
    if not model_cost:
        return 0

    updated = 0
    for name, info in model_cost.items():
        if not isinstance(info, dict):
            continue
        inp = info.get("input_cost_per_token")
        out = info.get("output_cost_per_token")
        if inp is None and out is None:
            continue
        in_m = (inp or 0) * 1_000_000
        out_m = (out or 0) * 1_000_000
        cached = info.get("cache_read_input_token_cost")
        cached_m = (cached * 1_000_000) if cached is not None else in_m * 0.5
        cw = info.get("cache_creation_input_token_cost")
        cw_m = (cw * 1_000_000) if cw is not None else in_m
        RATES[name] = {"in": in_m, "cached": cached_m, "cache_write": cw_m, "out": out_m}
        updated += 1
    return updated


def cache_hit_rate(cached_tokens: int, tokens_in: int) -> float:
    """Fraction of input tokens served from cache. 0 if tokens_in is 0."""
    return cached_tokens / tokens_in if tokens_in > 0 else 0.0


def cache_savings(
    model: str,
    cached_tokens: int = 0,
) -> float | None:
    """Estimate dollars saved by cache hits vs paying uncached input rate.

    Returns None if model isn't known.
    """
    rates = _lookup_rates(model)
    if rates is None:
        return None
    delta_per_token = (rates["in"] - rates["cached"]) / 1_000_000
    return max(0.0, round(cached_tokens * delta_per_token, 6))
