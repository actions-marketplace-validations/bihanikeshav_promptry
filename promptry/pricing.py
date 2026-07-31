"""Prompt pricing with cache awareness across providers.

**Catalog source of truth: [LiteLLM](https://github.com/BerriAI/litellm)
``model_cost``** (MIT License, BerriAI / contributors) — not a hand-maintained
list. promptry snapshots and serves those rates; credit LiteLLM when you
redistribute pricing data. We do **not** curate per-model $/1M rows in this repo.

How rates get into memory:

1. Packaged snapshot ``promptry/data/prices.json`` (generated from LiteLLM in CI)
2. Optional user refresh ``~/.promptry/prices.json`` (dashboard 24h pull or
   ``promptry prices --refresh`` / ``--litellm``)
3. Live ``refresh_rates_from_litellm()`` when litellm is installed

Rates are best-effort. Override via ``[pricing.*]`` in promptry.toml or
recompute from provider invoices for finance.
"""
from __future__ import annotations

import threading

# Filled on first use by ensure_prices_loaded() from the LiteLLM-generated
# package snapshot / user feed. Empty at import so we never hand-maintain a
# parallel catalog here.
RATES: dict[str, dict[str, float]] = {}


# Optional rename map — NOT from LiteLLM. Only for providers that keep the old
# request slug after they change what they bill (xAI 2026-05-15 retirement).
# Uncertain by nature; disable by clearing or not passing `when` to calculate_cost.
# https://docs.x.ai/developers/migration/may-15-retirement
REROUTES: dict[str, tuple[str, str]] = {
    "grok-4-fast":                 ("2026-05-15", "grok-4.3"),
    "grok-4-fast-non-reasoning":   ("2026-05-15", "grok-4.3"),
    "grok-4-fast-reasoning":       ("2026-05-15", "grok-4.3"),
    "grok-4-1-fast":               ("2026-05-15", "grok-4.3"),
    "grok-4-1-fast-non-reasoning": ("2026-05-15", "grok-4.3"),
    "grok-4-1-fast-reasoning":     ("2026-05-15", "grok-4.3"),
    "grok-4-0709":                 ("2026-05-15", "grok-4.3"),
    "grok-3":                      ("2026-05-15", "grok-4.3"),
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
    """Load the LiteLLM-derived catalog on first use. Idempotent.

    Order: package snapshot (shipped with the wheel) → user persisted file
    (dashboard refresh / CLI) merges on top. Every consumer of RATES must call
    this (or go through calculate_cost / resolve_model / is_known_model).
    """
    global _prices_loaded
    if _prices_loaded:
        return
    with _prices_load_lock:
        if _prices_loaded:
            return
        load_package_prices()
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


def _litellm_row_to_rate(info: dict) -> dict[str, float] | None:
    """Convert one litellm model_cost entry to promptry's per-1M shape."""
    if not isinstance(info, dict):
        return None
    inp = info.get("input_cost_per_token")
    out = info.get("output_cost_per_token")
    if inp is None and out is None:
        return None
    in_m = float(inp or 0) * 1_000_000
    out_m = float(out or 0) * 1_000_000
    cached = info.get("cache_read_input_token_cost")
    cached_m = (float(cached) * 1_000_000) if cached is not None else in_m * 0.5
    cw = info.get("cache_creation_input_token_cost")
    cw_m = (float(cw) * 1_000_000) if cw is not None else in_m
    return {"in": in_m, "cached": cached_m, "cache_write": cw_m, "out": out_m}


def _alias_keys(name: str) -> list[str]:
    """Extra keys so provider-prefixed litellm slugs also match bare names.

    e.g. ``azure_ai/grok-4`` → also ``grok-4``; ``openrouter/x-ai/grok-4`` →
    ``x-ai/grok-4`` and ``grok-4``. First registration wins (do not overwrite).
    """
    aliases: list[str] = []
    if "/" not in name:
        return aliases
    parts = name.split("/")
    # full tail after first segment, and final segment only
    if len(parts) >= 2:
        aliases.append("/".join(parts[1:]))
    if len(parts) >= 2:
        aliases.append(parts[-1])
    # de-dupe while preserving order, skip identity
    out: list[str] = []
    seen = {name}
    for a in aliases:
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def refresh_rates_from_litellm(*, replace: bool = True) -> int:
    """Pull current rates from litellm's ``model_cost`` map into RATES.

    This is the **authoritative** catalog when litellm is installed. Returns
    the number of litellm rows applied (aliases count separately in RATES but
    not in this return value). No-op (0) if litellm isn't installed.

    litellm stores per-token USD; we convert to per-1M
    ``{in, cached, cache_write, out}``.

    ``replace=True`` (default) clears the table first so we don't keep stale
    hand rows. REROUTES are left alone (not from litellm).
    """
    try:
        import litellm  # noqa
        model_cost = getattr(litellm, "model_cost", None)
    except Exception:
        return 0
    if not model_cost:
        return 0

    if replace:
        RATES.clear()

    updated = 0
    for name, info in model_cost.items():
        rate = _litellm_row_to_rate(info)
        if rate is None:
            continue
        RATES[name] = rate
        updated += 1
        for alias in _alias_keys(str(name)):
            # Prefer the first (usually more specific provider path) already set
            RATES.setdefault(alias, rate)

    if updated:
        from datetime import datetime, timezone
        PRICES_META["source"] = "litellm"
        PRICES_META["version"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        PRICES_META["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _recompute_rate_indexes()
    return updated


# --------------------------------------------------------------------------
# Price feed: LiteLLM catalog published as static JSON (no hand rate table).
#
# CI runs scripts/update_prices_feed.py (litellm → prices.json). That file is
# committed and also packaged under promptry/data/. Dashboards pull the GitHub
# raw URL on a timer; CLI `promptry prices --refresh` does the same on demand.
# `promptry prices --litellm` hits a local litellm install (no network).
# --------------------------------------------------------------------------

# Provenance of the rates currently in RATES. Updated when a feed/litellm is applied.
PRICES_META: dict = {"version": None, "source": "none", "updated": None}

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


def apply_feed(data: dict, *, replace: bool | None = None) -> int:
    """Apply a validated feed into RATES (and optional REROUTES).

    Full LiteLLM catalogs replace the table; small patch feeds merge so tests
    and ad-hoc additions still work. REROUTES in the feed are merged into the
    code-level map (never wiping the xAI defaults just because a feed omitted them).
    """
    validate_feed(data)
    src = str(data.get("source") or "feed")
    n_rates = len(data.get("rates") or {})
    if replace is None:
        replace = bool(
            data.get("mode") == "replace"
            or src.startswith("litellm")
            or n_rates > 100
        )
    if replace:
        RATES.clear()
    for name, r in data["rates"].items():
        RATES[name] = _normalize_rate(r)
    for slug, pair in (data.get("reroutes") or {}).items():
        REROUTES[slug] = (pair[0], pair[1])
    if data.get("version"):
        PRICES_META["version"] = data["version"]
    PRICES_META["source"] = src
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


def load_package_prices() -> int:
    """Load the LiteLLM snapshot shipped inside the package (promptry/data/prices.json).

    Returns number of rates applied, or 0 if missing/unreadable.
    """
    import json as _json
    try:
        from importlib.resources import files
        resource = files("promptry.data").joinpath("prices.json")
        if not resource.is_file():
            return 0
        data = _json.loads(resource.read_text(encoding="utf-8"))
        n = apply_feed(data, replace=True)
        if PRICES_META.get("source") in (None, "none", "feed"):
            PRICES_META["source"] = data.get("source") or "package"
        return n
    except Exception:
        return 0


def load_persisted_prices() -> int:
    """Apply a previously-refreshed ~/.promptry/prices.json if it exists.
    Returns the number applied (0 if no file or unreadable — never raises)."""
    import json as _json
    try:
        path = prices_file_path()
        if not path.is_file():
            return 0
        data = _json.loads(path.read_text(encoding="utf-8"))
        # User refresh replaces the package snapshot entirely when it's a full catalog
        return apply_feed(data)
    except Exception:
        return 0


# --------------------------------------------------------------------------
# Auto-refresh (dashboard): pull the published feed on startup + every N hours.
#
# The feed is a static JSON file in the promptry repo (prices.json on main),
# not a live vendor API. CI can update that file daily from litellm; each
# dashboard process then pulls it. Network is opt-out for the dashboard
# (default on) and always opt-in for the CLI (`promptry prices --refresh`).
# --------------------------------------------------------------------------

_DEFAULT_REFRESH_HOURS = 24.0
_auto_refresh_thread: threading.Thread | None = None
_auto_refresh_lock = threading.Lock()


def prices_feed_url() -> str:
    """Published feed URL. Override with PROMPTRY_PRICES_FEED_URL."""
    import os
    return (os.environ.get("PROMPTRY_PRICES_FEED_URL") or "").strip() or DEFAULT_FEED_URL


def prices_stale_hours() -> float | None:
    """How many hours since PRICES_META['updated'] (or the persisted file's
    mtime). None if we have no timestamp at all (bundled snapshot never
    refreshed)."""
    ensure_prices_loaded()
    from datetime import datetime, timezone
    updated = PRICES_META.get("updated")
    if updated:
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600.0)
        except ValueError:
            pass
    try:
        path = prices_file_path()
        if path.is_file():
            mtime = path.stat().st_mtime
            return max(0.0, (datetime.now(timezone.utc).timestamp() - mtime) / 3600.0)
    except OSError:
        pass
    return None


def prices_need_refresh(max_age_hours: float = _DEFAULT_REFRESH_HOURS) -> bool:
    """True if we should hit the network for a fresh feed."""
    age = prices_stale_hours()
    if age is None:
        return True  # never refreshed on this machine
    return age >= float(max_age_hours)


def auto_refresh_enabled(default: bool = False) -> bool:
    """PROMPTRY_PRICES_AUTO_REFRESH: 1/true/on enables, 0/false/off disables.
    When unset, returns `default` (dashboard passes True; CLI uses False)."""
    import os
    raw = (os.environ.get("PROMPTRY_PRICES_AUTO_REFRESH") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def auto_refresh_interval_hours() -> float:
    import os
    raw = (os.environ.get("PROMPTRY_PRICES_REFRESH_HOURS") or "").strip()
    if not raw:
        return _DEFAULT_REFRESH_HOURS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_REFRESH_HOURS


def maybe_refresh_prices(
    *,
    max_age_hours: float | None = None,
    url: str | None = None,
    force: bool = False,
    fetcher=None,
) -> dict | None:
    """Fetch + apply + persist the published feed if stale (or force=True).

    Returns the refresh_from_feed result dict, or None if skipped / failed.
    Never raises — a bad network or feed must not take down the dashboard.
    """
    import logging
    log = logging.getLogger("promptry.pricing")
    hours = _DEFAULT_REFRESH_HOURS if max_age_hours is None else float(max_age_hours)
    if not force and not prices_need_refresh(hours):
        return None
    try:
        ensure_prices_loaded()
        from datetime import datetime, timezone
        res = refresh_from_feed(url or prices_feed_url(), fetcher=fetcher, persist=True)
        # Ensure provenance carries a wall-clock timestamp even if the feed
        # omitted `updated` (older exports).
        if not PRICES_META.get("updated"):
            PRICES_META["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            # re-persist with the stamp so age checks work offline
            try:
                import json as _json
                path = prices_file_path()
                data = _json.loads(path.read_text(encoding="utf-8")) if path.is_file() else export_feed()
                data["updated"] = PRICES_META["updated"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass
        log.info(
            "price feed refreshed: %s rates from %s",
            res.get("count"), res.get("url"),
        )
        return res
    except Exception as exc:
        log.warning("price feed refresh failed: %s", exc)
        return None


def start_auto_refresh_loop(
    *,
    interval_hours: float | None = None,
    url: str | None = None,
    force_first: bool = True,
) -> threading.Thread | None:
    """Background thread: refresh once (if force_first) then every interval.

    Idempotent — a second call is a no-op while the first thread is alive.
    Daemon thread so it never blocks process exit.
    """
    global _auto_refresh_thread
    with _auto_refresh_lock:
        if _auto_refresh_thread is not None and _auto_refresh_thread.is_alive():
            return _auto_refresh_thread
        hours = auto_refresh_interval_hours() if interval_hours is None else float(interval_hours)
        hours = max(1.0, hours)
        feed_url = url  # capture

        def _loop() -> None:
            import time
            if force_first:
                maybe_refresh_prices(force=True, url=feed_url)
            while True:
                time.sleep(hours * 3600.0)
                maybe_refresh_prices(force=True, url=feed_url)

        t = threading.Thread(target=_loop, name="promptry-price-refresh", daemon=True)
        t.start()
        _auto_refresh_thread = t
        return t


def start_dashboard_price_refresh() -> threading.Thread | None:
    """Dashboard startup hook. Auto-refresh is ON by default for the dashboard
    process; set PROMPTRY_PRICES_AUTO_REFRESH=0 to stay offline.

    Skipped automatically under pytest so unit tests never hit the network.
    """
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") is not None:
        return None
    if os.environ.get("PROMPTRY_TESTING") in ("1", "true", "yes"):
        return None
    if not auto_refresh_enabled(default=True):
        return None
    return start_auto_refresh_loop(
        interval_hours=auto_refresh_interval_hours(),
        url=prices_feed_url(),
        force_first=True,
    )


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
