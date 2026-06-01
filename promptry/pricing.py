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
    # grok-4.3: as of 2026-05-15 xAI retired the grok-4*-fast slugs and routes
    # them here (https://docs.x.ai/developers/migration/may-15-retirement).
    # MUST be an explicit entry: the fuzzy prefix match would otherwise resolve
    # "grok-4.3"/"grok-4-3" to the "grok-4" tier ($3/$15) and badly overcount.
    # Published rate is in/out only; cached_read assumed 0.25x (xAI fast-tier
    # ratio), cache_write == in. Verify cached price against an invoice.
    "grok-4.3":                       {"in": 1.25, "cached": 0.31, "cache_write": 1.25, "out": 2.50},
    "grok-4-3":                       {"in": 1.25, "cached": 0.31, "cache_write": 1.25, "out": 2.50},
}


# Models a provider RETIRED and now silently serves as a different (priced)
# model from a date onward. The requested slug keeps resolving, so the only way
# to cost it correctly is to know the reroute: on/after the effective date, the
# slug bills at its replacement's rate. slug -> (effective_date, replacement).
# xAI, 2026-05-15: https://docs.x.ai/developers/migration/may-15-retirement
REROUTES = {
    "grok-4-fast":                 ("2026-05-15", "grok-4.3"),
    "grok-4-fast-non-reasoning":   ("2026-05-15", "grok-4.3"),
    "grok-4-fast-reasoning":       ("2026-05-15", "grok-4.3"),
    "grok-4-1-fast":               ("2026-05-15", "grok-4.3"),
    "grok-4-1-fast-non-reasoning": ("2026-05-15", "grok-4.3"),
    "grok-4-1-fast-reasoning":     ("2026-05-15", "grok-4.3"),
    "grok-4-0709":                 ("2026-05-15", "grok-4.3"),
    "grok-3":                      ("2026-05-15", "grok-4.3"),
    # Omitted until priced: grok-code-fast-1 -> grok-build-0.1 (xAI published no
    # rate for grok-build-0.1; rerouting to an unpriced model would silently
    # read $0, which is worse than leaving the slug at its own rate).
}


def _as_date(when):
    """Coerce a date / datetime / 'YYYY-MM-DD...' string to a date, else None."""
    from datetime import date, datetime
    if when is None:
        return None
    if isinstance(when, datetime):
        return when.date()
    if isinstance(when, date):
        return when
    s = str(when).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def resolve_model(model: str, when=None) -> str:
    """Map a requested slug to the model that ACTUALLY bills, honoring provider
    reroutes. `when` is the call's date; on/after a reroute's effective date the
    slug bills at its replacement. Without `when` we can't know which side of the
    cutoff a call is on, so no reroute is applied (back-compatible)."""
    if not model or when is None:
        return model
    reroute = REROUTES.get(model)
    if reroute is None:  # fuzzy: dated variants (grok-4-1-fast-non-reasoning-xxxx)
        for key in sorted(REROUTES, key=len, reverse=True):
            if model.startswith(key):
                reroute = REROUTES[key]
                break
    if reroute is None:
        return model
    eff, target = reroute
    d = _as_date(when)
    return target if (d is not None and d >= _as_date(eff)) else model


def calculate_cost(
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    when=None,
) -> float | None:
    """Return USD cost for a call, or None if model isn't in the rate table.

    tokens_in includes cached_tokens (the total input). We subtract to get
    uncached input before applying the standard rate.

    `when` (the call's date) activates provider reroutes: a retired slug billed
    at its replacement's rate on/after the reroute date (see REROUTES). Omit it
    and pricing is by the requested model name only.
    """
    rates = _lookup_rates(resolve_model(model, when))
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


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Good enough for a
    template-vs-payload breakdown without a tokenizer dependency."""
    if not text:
        return 0
    return max(1, round(len(text) / 4))


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
