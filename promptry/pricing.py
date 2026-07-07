"""Prompt pricing with cache awareness across providers.

Rates are best-effort snapshots. Users should override via config or recompute
from provider invoices for finance purposes.
"""
from __future__ import annotations

import threading

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
    "gpt-4.1-mini":     {"in": 0.40, "cached": 0.10, "cache_write": 0.40, "out": 1.60},
    # gpt-4.1-nano. MUST be an explicit entry: the fuzzy prefix match would
    # otherwise resolve it to the "gpt-4.1" tier ($2/$8) and overcount ~20x.
    # Some integrations log the litellm-style "openai/gpt-4.1-nano" slug (and it
    # surfaces as served_model), which can't prefix-match anything — price both.
    "gpt-4.1-nano":         {"in": 0.10, "cached": 0.025, "cache_write": 0.10, "out": 0.40},
    "openai/gpt-4.1-nano":  {"in": 0.10, "cached": 0.025, "cache_write": 0.10, "out": 0.40},
    # gpt-5.4-nano. Served as gpt-5.4-nano-2026-03-17, covered by prefix match.
    "gpt-5.4-nano":     {"in": 0.20, "cached": 0.02, "cache_write": 0.20, "out": 1.25},

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


# ---------------------------------------------------------------------------
# Perf indexes over RATES/REROUTES.
#
# resolve_model()/_lookup_rates() do a fuzzy prefix match ("gpt-4o-2024-11-20"
# -> "gpt-4o") by scanning keys longest-first so the more specific key wins.
# Re-sorting the keys on every call is wasted work once the tables stop
# changing at runtime (the common case), so precompute the sorted key lists
# once and only recompute when RATES/REROUTES are actually mutated (feed
# apply, litellm refresh, persisted-price load). Tests and other code also
# poke RATES/REROUTES directly without calling that hook, so both lookups
# fall back to a lazy re-sort whenever the precomputed list's length doesn't
# match the live dict — cheap to check, and self-healing if a hook is missed.
_rates_sorted_keys: list[str] = []
_reroutes_sorted_keys: list[str] = []
# Exact-match cache: literal queried model string -> the RATES key it resolves
# to (or None if nothing matches). Keyed on the *pre-resolution* model string,
# so repeated calls for the same served-model slug (e.g. every invocation of
# "gpt-4o-2024-11-20") skip the prefix scan entirely. We deliberately cache the
# MATCHED KEY, not the rates dict: the actual rates are re-read from RATES on
# every hit, so a value overwrite at the same key (`RATES["gpt-4o"] = {...}`,
# or a test-teardown `RATES.clear(); RATES.update(saved)`) is reflected
# immediately without needing _recompute_rate_indexes() to run. If the cached
# key has since been deleted from RATES (a same-length key swap), the hit is
# treated as a miss and the model is re-resolved from scratch. Also cleared
# whenever the indexes are recomputed (key-count changes).
_rates_cache: dict[str, str | None] = {}


def _recompute_rate_indexes() -> None:
    """Refresh the precomputed sorted-key lists and clear the resolution
    cache. Call after mutating RATES or REROUTES."""
    global _rates_sorted_keys, _reroutes_sorted_keys
    _rates_sorted_keys = sorted(RATES.keys(), key=len, reverse=True)
    _reroutes_sorted_keys = sorted(REROUTES.keys(), key=len, reverse=True)
    _rates_cache.clear()


_recompute_rate_indexes()  # initial computation for the bundled snapshot


def _rates_sort_keys() -> list[str]:
    """Sorted RATES keys (longest first), self-healing if RATES was mutated
    without going through _recompute_rate_indexes()."""
    if len(_rates_sorted_keys) != len(RATES):
        _recompute_rate_indexes()
    return _rates_sorted_keys


def _reroutes_sort_keys() -> list[str]:
    """Sorted REROUTES keys (longest first), same self-healing as above."""
    if len(_reroutes_sorted_keys) != len(REROUTES):
        _recompute_rate_indexes()
    return _reroutes_sorted_keys


# ---------------------------------------------------------------------------
# Lazy load of a previously-refreshed price feed.
#
# load_persisted_prices() reads and parses ~/.promptry/prices.json (or
# PROMPTRY_PRICES_FILE). That's disk I/O on every import, paid even by
# short-lived CLI invocations that never touch pricing. Defer it to the
# first call of calculate_cost()/resolve_model()/is_known_model() (the three
# entry points that need current rates), guarded so it only ever runs once.
# ---------------------------------------------------------------------------
_prices_loaded = False
_prices_load_lock = threading.Lock()


def ensure_prices_loaded() -> None:
    """Apply a previously-refreshed price feed on first use. Idempotent and
    safe to call from every pricing entry point — a no-op after the first
    call. Every consumer of RATES/REROUTES/PRICES_META (CLI, dashboard,
    projectconfig overrides, storage cost aggregation) must call this before
    reading those globals directly, since only calculate_cost/resolve_model/
    is_known_model trigger it implicitly."""
    global _prices_loaded
    if _prices_loaded:
        return
    with _prices_load_lock:
        if _prices_loaded:
            return
        load_persisted_prices()
        _prices_loaded = True


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
    ensure_prices_loaded()
    if not model or when is None:
        return model
    reroute = REROUTES.get(model)
    if reroute is None:  # fuzzy: dated variants (grok-4-1-fast-non-reasoning-xxxx)
        for key in _reroutes_sort_keys():
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
    """Fuzzy lookup: 'gpt-4o-2024-11-20' -> 'gpt-4o' via prefix matching.

    Exact-match cache keyed on the literal queried string, mapping to the
    RATES key it resolves to (not the rates dict itself) so repeated calls
    for the same served-model slug skip the prefix scan without ever serving
    a stale value: on a hit we always re-read RATES[key] fresh, so a same-key
    value overwrite (`RATES["gpt-4o"] = {...}`, or a test-teardown
    `RATES.clear(); RATES.update(saved)`) is picked up immediately, with no
    dependency on _recompute_rate_indexes() having been called. If the cached
    key was since deleted from RATES (a same-length key swap), the fresh read
    comes back None and we treat that as a cache miss and re-resolve from
    scratch, so a deleted key's rates can never be handed back.
    """
    ensure_prices_loaded()
    if not model:
        return None
    if model in _rates_cache:
        key = _rates_cache[model]
        if key is not None:
            result = RATES.get(key)
            if result is not None:
                return result
            # Cached key no longer exists (deleted); fall through and
            # re-resolve instead of trusting the stale mapping.
        else:
            return None
    if model in RATES:
        key = model
        result = RATES[model]
    else:
        key = None
        result = None
        for candidate in _rates_sort_keys():
            if model.startswith(candidate):
                candidate_result = RATES.get(candidate)
                if candidate_result is None:
                    # The sorted-key index only self-heals on a length
                    # mismatch (see _rates_sort_keys), so a same-key-count
                    # swap (del one key, add another) can leave a deleted
                    # key in the stale list. Skip it rather than treat it
                    # as an authoritative match — keep scanning.
                    continue
                key = candidate
                result = candidate_result
                break
    _rates_cache[model] = key
    return result


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
    if updated:
        _recompute_rate_indexes()
    return updated


# --------------------------------------------------------------------------
# Price feed: bundled snapshot + opt-in refresh, no hosted service.
#
# The bundled RATES above ship with the package. A user can refresh them from
# a STATIC published feed (a plain JSON file the maintainer commits/publishes)
# or from a locally-installed litellm. A refresh writes the result to
# ~/.promptry/prices.json, which is loaded on import to override the bundled
# snapshot. Nothing phones home unless the user runs `promptry prices --refresh`.
# --------------------------------------------------------------------------

# Provenance of the rates currently in RATES. Updated when a feed is applied.
PRICES_META: dict = {"version": "2026-06-01", "source": "bundled", "updated": None}

# Conventional location of the maintainer-published feed. Override with --url.
DEFAULT_FEED_URL = "https://raw.githubusercontent.com/bihanikeshav/promptry/main/prices.json"


def prices_file_path():
    """Where a refreshed price feed is persisted. Honors PROMPTRY_PRICES_FILE
    (handy for tests); defaults to ~/.promptry/prices.json."""
    import os
    from pathlib import Path
    override = os.environ.get("PROMPTRY_PRICES_FILE")
    if override:
        return Path(override)
    return Path.home() / ".promptry" / "prices.json"


def _normalize_rate(r: dict) -> dict:
    """Coerce a feed rate (which may carry only in/out) to promptry's full
    {in,cached,cache_write,out} shape, mirroring the litellm fallback ratios."""
    inp = float(r["in"])
    out = float(r["out"])
    cached = float(r["cached"]) if r.get("cached") is not None else round(inp * 0.5, 6)
    cw = float(r["cache_write"]) if r.get("cache_write") is not None else inp
    return {"in": inp, "cached": cached, "cache_write": cw, "out": out}


def validate_feed(data: dict) -> None:
    """Raise ValueError unless `data` is a well-formed price feed: a dict with a
    `rates` mapping of model -> rate, each rate carrying numeric `in` and `out`
    (cached/cache_write optional), and an optional `reroutes` mapping of
    slug -> [effective_date, replacement]."""
    if not isinstance(data, dict):
        raise ValueError("feed must be a JSON object")
    rates = data.get("rates")
    if not isinstance(rates, dict) or not rates:
        raise ValueError("feed.rates must be a non-empty object")
    for name, r in rates.items():
        if not isinstance(r, dict):
            raise ValueError(f"rate for {name!r} must be an object")
        for k in ("in", "out"):
            if not isinstance(r.get(k), (int, float)):
                raise ValueError(f"rate for {name!r} is missing numeric {k!r}")
    reroutes = data.get("reroutes", {})
    if not isinstance(reroutes, dict):
        raise ValueError("feed.reroutes must be an object")
    for slug, pair in reroutes.items():
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(x, str) for x in pair)):
            raise ValueError(f"reroute for {slug!r} must be [effective_date, replacement]")


def export_feed() -> dict:
    """Serialize the current rate table as a feed the maintainer can publish.
    `promptry prices --export prices.json` then commit it; clients refresh from
    its raw URL. Reroutes are emitted as [effective_date, replacement] lists."""
    return {
        "version": PRICES_META.get("version"),
        "updated": PRICES_META.get("updated"),
        "rates": {k: dict(v) for k, v in RATES.items()},
        "reroutes": {k: [eff, target] for k, (eff, target) in REROUTES.items()},
    }


def diff_rates(old: dict, new: dict) -> dict:
    """Compare two model->rate maps. Returns added/removed model names and a list
    of (model, field, old_value, new_value) for changed rate fields."""
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for name in sorted(set(old) & set(new)):
        o, n = old[name], new[name]
        for field in ("in", "cached", "cache_write", "out"):
            if o.get(field) != n.get(field):
                changed.append((name, field, o.get(field), n.get(field)))
    return {"added": added, "removed": removed, "changed": changed}


def apply_feed(data: dict) -> int:
    """Merge a validated feed into RATES/REROUTES and update PRICES_META.
    Returns the number of rates applied. Existing models are overwritten;
    models absent from the feed are left untouched (feeds are additive)."""
    validate_feed(data)
    for name, r in data["rates"].items():
        RATES[name] = _normalize_rate(r)
    for slug, pair in data.get("reroutes", {}).items():
        REROUTES[slug] = (pair[0], pair[1])
    if data.get("version"):
        PRICES_META["version"] = data["version"]
    PRICES_META["source"] = data.get("source", "feed")
    PRICES_META["updated"] = data.get("updated") or PRICES_META.get("updated")
    _recompute_rate_indexes()
    return len(data["rates"])


def _http_get(url: str) -> str:
    """Fetch a URL's body as text. Isolated so tests can inject a fetcher."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (user-invoked)
        return resp.read().decode("utf-8")


def refresh_from_feed(url: str | None = None, fetcher=None, persist: bool = True) -> dict:
    """Opt-in refresh: fetch a static JSON price feed, validate it, apply it,
    and (by default) persist it to prices_file_path() so it survives restarts.
    `fetcher(url) -> str` is injectable for tests. Returns a result dict with the
    source url, count, and a diff vs the rates that were in effect before."""
    import json as _json
    url = url or DEFAULT_FEED_URL
    fetch = fetcher or _http_get
    before = {k: dict(v) for k, v in RATES.items()}
    raw = fetch(url)
    data = raw if isinstance(raw, dict) else _json.loads(raw)
    validate_feed(data)
    count = apply_feed(data)
    if persist:
        path = prices_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    return {"url": url, "count": count, "diff": diff_rates(before, {k: dict(v) for k, v in RATES.items()})}


def load_persisted_prices() -> int:
    """Apply a previously-refreshed ~/.promptry/prices.json if it exists, so
    refreshed rates take effect on import. Returns the number applied (0 if no
    file or it's unreadable — a bad cache must never break cost computation)."""
    import json as _json
    try:
        path = prices_file_path()
        if not path.is_file():
            return 0
        data = _json.loads(path.read_text(encoding="utf-8"))
        return apply_feed(data)
    except Exception:
        return 0


def build_cost_report(
    storage,
    days: int = 7,
    name: str | None = None,
    model: str | None = None,
) -> dict:
    """Aggregate cost data for the `cost-report` CLI/dashboard: storage's
    summary/by_name/by_date breakdown, plus a count of calls on un-priced
    models (which silently cost $0 and are worth flagging).
    """
    data = storage.get_cost_data(days=days, name=name, model=model)

    unpriced_calls = 0
    if hasattr(storage, "list_invocations"):
        inv_rows = storage.list_invocations(name=name, days=days, limit=100000)
        if model:
            inv_rows = [r for r in inv_rows if r.get("model") == model]
        unpriced_calls = sum(
            1 for r in inv_rows if r.get("model") and not is_known_model(r["model"])
        )
    data["unpriced_calls"] = unpriced_calls
    return data


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
