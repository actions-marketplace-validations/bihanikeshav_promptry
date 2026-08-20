"""Tests for promptry.pricing — cache-aware cost math.

Product catalog = LiteLLM snapshot. These tests pin a small **fixture overlay**
of rates so dollar assertions stay stable when litellm updates.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timezone

from promptry import pricing
from promptry.pricing import (
    RATES,
    REROUTES,
    calculate_cost,
    cache_hit_rate,
    cache_savings,
    resolve_model,
    _lookup_rates,
)

# Stable numbers for unit tests of math / prefix / reroute behavior only.
_FIXTURE_RATES = {
    "gpt-4o": {"in": 2.50, "cached": 1.25, "cache_write": 2.50, "out": 10.00},
    "gpt-4o-mini": {"in": 0.15, "cached": 0.075, "cache_write": 0.15, "out": 0.60},
    "claude-sonnet-4": {"in": 3.00, "cached": 0.30, "cache_write": 3.75, "out": 15.00},
    "gemini-2.5-flash": {"in": 0.30, "cached": 0.075, "cache_write": 0.30, "out": 2.50},
    "grok-2": {"in": 2.00, "cached": 0.50, "cache_write": 2.00, "out": 10.00},
    "grok-3": {"in": 3.00, "cached": 0.75, "cache_write": 3.00, "out": 15.00},
    "grok-4": {"in": 3.00, "cached": 0.75, "cache_write": 3.00, "out": 15.00},
    "grok-4.3": {"in": 1.25, "cached": 0.31, "cache_write": 1.25, "out": 2.50},
    "grok-4-3": {"in": 1.25, "cached": 0.31, "cache_write": 1.25, "out": 2.50},
    "grok-4-fast": {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-fast-non-reasoning": {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
    "grok-4-1-fast-non-reasoning": {"in": 0.20, "cached": 0.05, "cache_write": 0.20, "out": 0.50},
}


@pytest.fixture(autouse=True)
def _fixture_rate_overlay():
    pricing.ensure_prices_loaded()
    for name, rate in _FIXTURE_RATES.items():
        RATES[name] = dict(rate)
    pricing._recompute_rate_indexes()
    yield


class TestLookupRates:
    def test_exact_match(self):
        assert _lookup_rates("gpt-4o") == RATES["gpt-4o"]

    def test_prefix_match_dated_openai_model(self):
        # "gpt-4o-2024-11-20" should fall back to "gpt-4o"
        assert _lookup_rates("gpt-4o-2024-11-20") == RATES["gpt-4o"]

    def test_prefix_match_prefers_longer_key(self):
        # "gpt-4o-mini-2024-07-18" must match "gpt-4o-mini", not "gpt-4o"
        assert _lookup_rates("gpt-4o-mini-2024-07-18") == RATES["gpt-4o-mini"]

    def test_anthropic_prefix(self):
        assert _lookup_rates("claude-sonnet-4-20250514") == RATES["claude-sonnet-4"]

    def test_unknown_model(self):
        assert _lookup_rates("totally-made-up-model") is None

    def test_empty_model(self):
        assert _lookup_rates("") is None


class TestGrok43Retirement:
    """Regression: xAI retired grok-4*-fast on 2026-05-15 -> grok-4.3.
    grok-4.3 MUST resolve to its own $1.25/$2.50 tier, not the grok-4 $3/$15
    tier the fuzzy prefix match would otherwise pick."""

    def test_grok_43_exact_dot_form(self):
        assert _lookup_rates("grok-4.3") == {"in": 1.25, "cached": 0.31, "cache_write": 1.25, "out": 2.50}

    def test_grok_43_dash_form(self):
        assert _lookup_rates("grok-4-3") == RATES["grok-4.3"]

    def test_grok_43_not_misresolved_to_grok4_tier(self):
        # the bug we're guarding: prefix match -> "grok-4" ($3/$15)
        assert _lookup_rates("grok-4.3") != RATES["grok-4"]
        assert _lookup_rates("grok-4.3")["out"] == 2.50  # not 15.00

    def test_grok_43_dated_suffix_prefix_match(self):
        # a served name like "grok-4.3-2026xx" still resolves to the 4.3 tier
        assert _lookup_rates("grok-4.3-20260515") == RATES["grok-4.3"]

    def test_retired_fast_slug_still_cheap(self):
        # the requested slug itself is unchanged/cheap; the reroute cost only
        # shows up once you price off the served model
        assert _lookup_rates("grok-4-fast-non-reasoning")["in"] == 0.20

    def test_reroute_cost_multiple(self):
        # same tokens, fast-rate vs grok-4.3: ~6x in, 5x out
        toks = dict(tokens_in=1_000_000, tokens_out=1_000_000)
        fast = calculate_cost("grok-4-fast-non-reasoning", **toks)
        real = calculate_cost("grok-4.3", **toks)
        assert real / fast == pytest.approx((1.25 + 2.50) / (0.20 + 0.50), rel=1e-6)


class TestDateAwareReroute:
    """After 2026-05-15 the retired grok-4*-fast slugs bill at grok-4.3's rate,
    keyed on the call's date (the slug itself never changes)."""

    SLUG = "grok-4-1-fast-non-reasoning"

    def test_resolves_to_replacement_after_cutoff(self):
        assert resolve_model(self.SLUG, "2026-05-20") == "grok-4.3"

    def test_unchanged_before_cutoff(self):
        assert resolve_model(self.SLUG, "2026-05-10") == self.SLUG

    def test_cutoff_day_is_inclusive(self):
        assert resolve_model(self.SLUG, "2026-05-15") == "grok-4.3"

    def test_no_date_means_no_reroute(self):
        # back-compatible: without a date we can't know which side of the cutoff
        assert resolve_model(self.SLUG, None) == self.SLUG

    def test_accepts_datetime_and_iso_string(self):
        assert resolve_model(self.SLUG, datetime(2026, 5, 20, tzinfo=timezone.utc)) == "grok-4.3"
        assert resolve_model(self.SLUG, "2026-05-20 14:00:00") == "grok-4.3"

    def test_dated_variant_prefix_matches(self):
        assert resolve_model("grok-4-1-fast-non-reasoning-0709", "2026-05-20") == "grok-4.3"

    def test_non_rerouted_model_untouched(self):
        assert resolve_model("gpt-4o", "2026-05-20") == "gpt-4o"
        assert resolve_model("grok-4.3", "2026-05-20") == "grok-4.3"

    def test_calculate_cost_is_date_aware(self):
        toks = dict(tokens_in=1_000_000, tokens_out=1_000_000)
        after = calculate_cost(self.SLUG, when="2026-05-20", **toks)
        before = calculate_cost(self.SLUG, when="2026-05-10", **toks)
        assert after == pytest.approx(1.25 + 2.50)    # grok-4.3 rate
        assert before == pytest.approx(0.20 + 0.50)   # old fast rate
        # omitting `when` stays back-compatible (no reroute)
        assert calculate_cost(self.SLUG, **toks) == pytest.approx(0.20 + 0.50)

    def test_every_reroute_target_is_priced(self):
        # a reroute pointing at a model with no rate would silently cost $0
        for _slug, (_eff, target) in REROUTES.items():
            assert _lookup_rates(target) is not None, f"{target} missing from RATES"


class TestCalculateCostOpenAI:
    def test_basic_cost_no_cache(self):
        # 1M in, 1M out at gpt-4o rates
        cost = calculate_cost("gpt-4o", tokens_in=1_000_000, tokens_out=1_000_000)
        assert cost == pytest.approx(2.50 + 10.00)

    def test_cached_discount(self):
        # 1000 tokens in, 800 cached
        cost = calculate_cost(
            "gpt-4o", tokens_in=1000, tokens_out=0, cached_tokens=800
        )
        # 200 @ 2.50/M + 800 @ 1.25/M
        expected = (200 / 1_000_000) * 2.50 + (800 / 1_000_000) * 1.25
        assert cost == pytest.approx(round(expected, 6))

    def test_gpt4o_mini_known_rate(self):
        cost = calculate_cost("gpt-4o-mini", tokens_in=1_000_000, tokens_out=1_000_000)
        assert cost == pytest.approx(0.15 + 0.60)


class TestCalculateCostAnthropic:
    def test_anthropic_cache_write(self):
        # Claude Sonnet: 1000 total in = 500 uncached + 500 cache write
        cost = calculate_cost(
            "claude-sonnet-4",
            tokens_in=1000,
            tokens_out=0,
            cache_write_tokens=500,
        )
        # uncached_in = 1000 - 0 - 500 = 500
        # 500 @ 3.00/M + 500 @ 3.75/M
        expected = (500 / 1_000_000) * 3.00 + (500 / 1_000_000) * 3.75
        assert cost == pytest.approx(round(expected, 6))

    def test_anthropic_cache_read_heavy_discount(self):
        # 1000 total in, 900 cache read
        cost = calculate_cost(
            "claude-sonnet-4",
            tokens_in=1000,
            tokens_out=0,
            cached_tokens=900,
        )
        expected = (100 / 1_000_000) * 3.00 + (900 / 1_000_000) * 0.30
        assert cost == pytest.approx(round(expected, 6))


class TestCalculateCostGemini:
    def test_gemini_flash(self):
        cost = calculate_cost(
            "gemini-2.5-flash", tokens_in=1_000_000, tokens_out=1_000_000
        )
        assert cost == pytest.approx(0.30 + 2.50)


class TestCalculateCostGrok:
    def test_grok_2(self):
        cost = calculate_cost("grok-2", tokens_in=1_000_000, tokens_out=0)
        assert cost == pytest.approx(2.00)

    def test_grok_3_with_cache(self):
        cost = calculate_cost(
            "grok-3", tokens_in=1000, tokens_out=0, cached_tokens=500
        )
        expected = (500 / 1_000_000) * 3.00 + (500 / 1_000_000) * 0.75
        assert cost == pytest.approx(round(expected, 6))


class TestCalculateCostEdgeCases:
    def test_unknown_model_returns_none(self):
        assert calculate_cost("bogus-9000", tokens_in=1000, tokens_out=1000) is None

    def test_zero_tokens(self):
        assert calculate_cost("gpt-4o", tokens_in=0, tokens_out=0) == 0.0

    def test_cached_greater_than_total_does_not_go_negative(self):
        # Defensive: if cached_tokens > tokens_in, uncached clamps to 0
        cost = calculate_cost(
            "gpt-4o", tokens_in=100, tokens_out=0, cached_tokens=500
        )
        # uncached clamped to 0, cached fully counted
        expected = (500 / 1_000_000) * 1.25
        assert cost == pytest.approx(round(expected, 6))


class TestCacheHitRate:
    def test_half(self):
        assert cache_hit_rate(500, 1000) == 0.5

    def test_full_hit(self):
        assert cache_hit_rate(1000, 1000) == 1.0

    def test_zero_input(self):
        assert cache_hit_rate(0, 0) == 0.0

    def test_no_hits(self):
        assert cache_hit_rate(0, 1000) == 0.0


class TestRateIndexPerf:
    """perf: sorted-key lists are precomputed, not re-sorted every call, with
    a self-healing fallback for direct RATES/REROUTES mutation, plus a small
    exact-match cache for resolved model -> rates."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        rates = {k: dict(v) for k, v in RATES.items()}
        reroutes = dict(REROUTES)
        yield
        RATES.clear()
        RATES.update(rates)
        REROUTES.clear()
        REROUTES.update(reroutes)
        pricing._recompute_rate_indexes()

    def test_precomputed_index_matches_live_dict_length(self):
        pricing._recompute_rate_indexes()
        assert len(pricing._rates_sorted_keys) == len(RATES)
        assert len(pricing._reroutes_sorted_keys) == len(REROUTES)

    def test_lookup_still_correct_after_direct_rates_mutation(self):
        # Tests (and some legacy call sites) poke RATES directly, bypassing
        # _recompute_rate_indexes(). The length-mismatch fallback must still
        # resolve correctly rather than using a stale key list.
        RATES["zzz-brand-new-model"] = {"in": 9.0, "cached": 4.5, "cache_write": 9.0, "out": 20.0}
        assert _lookup_rates("zzz-brand-new-model-dated-2099") == RATES["zzz-brand-new-model"]

    def test_resolved_rates_cache_returns_equal_result(self):
        # Repeated lookups of the same non-canonical slug must be idempotent
        # and consistent regardless of caching.
        r1 = _lookup_rates("gpt-4o-2024-11-20")
        r2 = _lookup_rates("gpt-4o-2024-11-20")
        assert r1 == r2 == RATES["gpt-4o"]

    def test_cache_invalidated_by_apply_feed(self):
        # Prime the cache with the old rate, then override it via a feed —
        # the cached entry must not shadow the new rate.
        assert _lookup_rates("gpt-4o") == RATES["gpt-4o"]
        pricing.apply_feed({"rates": {"gpt-4o": {"in": 1.0, "out": 2.0}}})
        try:
            assert _lookup_rates("gpt-4o")["in"] == pytest.approx(1.0)
        finally:
            pass  # autouse fixture restores RATES/REROUTES + indexes

    def test_cache_reflects_same_key_value_overwrite_without_recompute(self):
        # Prime the cache, then overwrite RATES["gpt-4o"] in place (same key,
        # same key count) WITHOUT calling _recompute_rate_indexes(). The
        # cached resolution must not shadow the new value.
        assert _lookup_rates("gpt-4o") == RATES["gpt-4o"]
        RATES["gpt-4o"] = {"in": 999.0, "cached": 9.0, "cache_write": 9.0, "out": 999.0}
        assert _lookup_rates("gpt-4o")["in"] == 999.0
        assert _lookup_rates("gpt-4o")["out"] == 999.0

    def test_cache_reflects_clear_and_update_teardown_pattern(self):
        # Mirrors tests/test_cli.py TestPricesCLI._isolate_prices: a fixture
        # snapshots RATES, a test mutates it, and teardown restores via
        # RATES.clear(); RATES.update(saved) — with no explicit recompute
        # call. If teardown restores DIFFERENT values for the same key set,
        # the cache must not keep serving what was cached before the clear.
        assert _lookup_rates("gpt-4o") == RATES["gpt-4o"]
        saved_key_count = len(RATES)
        changed = {k: dict(v) for k, v in RATES.items()}
        changed["gpt-4o"] = {"in": 111.0, "cached": 1.0, "cache_write": 1.0, "out": 222.0}
        RATES.clear()
        RATES.update(changed)
        assert len(RATES) == saved_key_count  # same key set, no recompute triggered
        assert _lookup_rates("gpt-4o")["in"] == pytest.approx(111.0)
        assert _lookup_rates("gpt-4o")["out"] == pytest.approx(222.0)

    def test_cache_does_not_leak_deleted_keys_rates(self):
        # Use a unique slug so other litellm keys (e.g. gpt-4) can't prefix-match.
        slug = "zzunique-price-model"
        RATES[slug] = {"in": 1.0, "cached": 0.5, "cache_write": 1.0, "out": 2.0}
        pricing._recompute_rate_indexes()
        old_rate = dict(_lookup_rates(slug))
        del RATES[slug]
        RATES["zzz-replacement-model"] = old_rate
        # same key count as before this swap only if we also drop one elsewhere —
        # force recompute off by matching length: add and delete within same size
        result = _lookup_rates(slug)
        assert result is None
        assert _lookup_rates("zzz-replacement-model") == old_rate


class TestLazyLoad:
    def test_ensure_prices_loaded_is_idempotent(self):
        # Calling it repeatedly must not raise and must leave RATES sane.
        pricing.ensure_prices_loaded()
        pricing.ensure_prices_loaded()
        assert "gpt-4o" in RATES

    def test_known_pricing_entry_points_trigger_it(self):
        # calculate_cost/resolve_model/is_known_model must each be safe to
        # call without an explicit ensure_prices_loaded() first.
        assert pricing.is_known_model("gpt-4o") is True
        assert resolve_model("gpt-4o", "2026-01-01") == "gpt-4o"
        assert calculate_cost("gpt-4o", tokens_in=0, tokens_out=0) == 0.0


class TestCacheSavings:
    def test_openai_savings(self):
        # gpt-4o: uncached 2.50, cached 1.25 -> saves 1.25/M per cached token
        savings = cache_savings("gpt-4o", cached_tokens=1_000_000)
        assert savings == pytest.approx(1.25)

    def test_anthropic_savings(self):
        # claude-sonnet-4: 3.00 - 0.30 = 2.70/M
        savings = cache_savings("claude-sonnet-4", cached_tokens=1_000_000)
        assert savings == pytest.approx(2.70)

    def test_unknown_model_returns_none(self):
        assert cache_savings("bogus", cached_tokens=1000) is None

    def test_zero_cached(self):
        assert cache_savings("gpt-4o", cached_tokens=0) == 0.0
