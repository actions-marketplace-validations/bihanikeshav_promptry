"""Tests for the opt-in price feed: bundled snapshot + refresh, no hosted service."""
from __future__ import annotations

import json

import pytest

from promptry import pricing


@pytest.fixture(autouse=True)
def _restore_pricing_globals():
    """The feed functions mutate module-global RATES/REROUTES/PRICES_META.
    Snapshot and restore so tests stay independent."""
    pricing.ensure_prices_loaded()
    rates = {k: dict(v) for k, v in pricing.RATES.items()}
    reroutes = dict(pricing.REROUTES)
    meta = dict(pricing.PRICES_META)
    loaded = pricing._prices_loaded
    yield
    pricing.RATES.clear()
    pricing.RATES.update(rates)
    pricing.REROUTES.clear()
    pricing.REROUTES.update(reroutes)
    pricing.PRICES_META.clear()
    pricing.PRICES_META.update(meta)
    pricing._prices_loaded = loaded
    pricing._recompute_rate_indexes()


class TestExportFeed:
    def test_export_is_json_serializable_and_complete(self):
        pricing.ensure_prices_loaded()
        feed = pricing.export_feed()
        assert set(feed) >= {"version", "rates", "reroutes"}
        assert len(feed["rates"]) > 0
        # code-level xAI reroutes still serialize from REROUTES
        assert feed["reroutes"]["grok-3"] == ["2026-05-15", "grok-4.3"]
        json.dumps(feed)  # must not raise

    def test_export_round_trips_through_apply(self):
        pricing.ensure_prices_loaded()
        feed = pricing.export_feed()
        before = {k: dict(v) for k, v in pricing.RATES.items()}
        pricing.apply_feed(feed, replace=True)
        assert pricing.RATES == before


class TestValidateFeed:
    def test_accepts_minimal_in_out(self):
        pricing.validate_feed({"rates": {"m": {"in": 1.0, "out": 2.0}}})

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError):
            pricing.validate_feed([1, 2, 3])

    def test_rejects_empty_rates(self):
        with pytest.raises(ValueError):
            pricing.validate_feed({"rates": {}})

    def test_rejects_missing_out(self):
        with pytest.raises(ValueError, match="out"):
            pricing.validate_feed({"rates": {"m": {"in": 1.0}}})

    def test_rejects_bad_reroute_shape(self):
        with pytest.raises(ValueError, match="reroute"):
            pricing.validate_feed({
                "rates": {"m": {"in": 1.0, "out": 2.0}},
                "reroutes": {"old": ["only-one"]},
            })


class TestDiffRates:
    def test_reports_added_removed_changed(self):
        old = {
            "a": {"in": 1, "cached": 0.5, "cache_write": 1, "out": 2},
            "b": {"in": 3, "cached": 1.5, "cache_write": 3, "out": 6},
        }
        new = {
            "a": {"in": 1, "cached": 0.5, "cache_write": 1, "out": 9},  # out changed
            "c": {"in": 5, "cached": 2.5, "cache_write": 5, "out": 10},  # added
        }
        d = pricing.diff_rates(old, new)
        assert d["added"] == ["c"]
        assert d["removed"] == ["b"]
        assert ("a", "out", 2, 9) in d["changed"]


class TestApplyFeed:
    def test_adds_new_model_and_fills_defaults(self):
        pricing.apply_feed({"rates": {"newmodel-x": {"in": 4.0, "out": 8.0}}})
        assert pricing.is_known_model("newmodel-x")
        r = pricing.RATES["newmodel-x"]
        assert r["cached"] == pytest.approx(2.0)   # default in*0.5
        assert r["cache_write"] == pytest.approx(4.0)  # default == in

    def test_adds_reroute(self):
        pricing.apply_feed({
            "rates": {"replacement-y": {"in": 1.0, "out": 2.0}},
            "reroutes": {"retired-z": ["2026-07-01", "replacement-y"]},
        })
        assert pricing.REROUTES["retired-z"] == ("2026-07-01", "replacement-y")

    def test_updates_provenance(self):
        pricing.apply_feed({
            "version": "2026-12-31", "source": "feed",
            "rates": {"m": {"in": 1.0, "out": 2.0}},
        })
        assert pricing.PRICES_META["version"] == "2026-12-31"
        assert pricing.PRICES_META["source"] == "feed"


class TestRefreshFromFeed:
    def test_fetch_validate_apply_persist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(tmp_path / "prices.json"))
        feed = {
            "version": "2026-09-09",
            "rates": {"fake-model-x": {"in": 9.0, "out": 18.0}},
            "reroutes": {},
        }
        res = pricing.refresh_from_feed("http://example/prices.json",
                                        fetcher=lambda url: json.dumps(feed))
        assert pricing.is_known_model("fake-model-x")
        assert (tmp_path / "prices.json").is_file()
        assert "fake-model-x" in res["diff"]["added"]
        assert res["count"] == 1

    def test_persisted_feed_loaded_on_demand(self, tmp_path, monkeypatch):
        path = tmp_path / "prices.json"
        path.write_text(json.dumps({
            "rates": {"persisted-model": {"in": 7.0, "out": 14.0}},
        }), encoding="utf-8")
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(path))
        assert pricing.load_persisted_prices() == 1
        assert pricing.is_known_model("persisted-model")

    def test_bad_persisted_file_is_ignored(self, tmp_path, monkeypatch):
        path = tmp_path / "prices.json"
        path.write_text("{ not valid json", encoding="utf-8")
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(path))
        assert pricing.load_persisted_prices() == 0  # never raises

    def test_refreshed_price_changes_cost(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(tmp_path / "prices.json"))
        # reprice gpt-4o-mini and confirm calculate_cost reflects it
        pricing.refresh_from_feed("http://x", fetcher=lambda url: json.dumps({
            "rates": {"gpt-4o-mini": {"in": 1.0, "out": 2.0}},
        }))
        cost = pricing.calculate_cost("gpt-4o-mini", tokens_in=1_000_000, tokens_out=0)
        assert cost == pytest.approx(1.0)


class TestAutoRefresh:
    def test_need_refresh_when_never_updated(self, monkeypatch):
        pricing.PRICES_META["updated"] = None
        monkeypatch.delenv("PROMPTRY_PRICES_FILE", raising=False)
        # point at a missing file so mtime can't save us
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", "/tmp/promptry-no-such-prices.json")
        pricing._prices_loaded = True  # skip load
        assert pricing.prices_need_refresh(max_age_hours=24) is True

    def test_stale_after_age(self, monkeypatch):
        pricing.PRICES_META["updated"] = "2000-01-01T00:00:00Z"
        pricing._prices_loaded = True
        assert pricing.prices_need_refresh(max_age_hours=24) is True

    def test_fresh_skips_network(self, monkeypatch):
        from datetime import datetime, timezone
        pricing.PRICES_META["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pricing._prices_loaded = True
        called = {"n": 0}

        def boom(_url):
            called["n"] += 1
            raise AssertionError("should not fetch")

        res = pricing.maybe_refresh_prices(max_age_hours=24, fetcher=boom, force=False)
        assert res is None
        assert called["n"] == 0

    def test_force_refreshes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(tmp_path / "prices.json"))
        pricing.PRICES_META["updated"] = "2099-01-01T00:00:00Z"  # "fresh"
        pricing._prices_loaded = True
        feed = {"version": "2099-02-02", "rates": {"auto-model-z": {"in": 1.0, "out": 2.0}}}
        res = pricing.maybe_refresh_prices(
            force=True,
            fetcher=lambda _u: json.dumps(feed),
        )
        assert res is not None
        assert pricing.is_known_model("auto-model-z")

    def test_auto_refresh_env_flag(self, monkeypatch):
        monkeypatch.delenv("PROMPTRY_PRICES_AUTO_REFRESH", raising=False)
        assert pricing.auto_refresh_enabled(default=True) is True
        assert pricing.auto_refresh_enabled(default=False) is False
        monkeypatch.setenv("PROMPTRY_PRICES_AUTO_REFRESH", "0")
        assert pricing.auto_refresh_enabled(default=True) is False
        monkeypatch.setenv("PROMPTRY_PRICES_AUTO_REFRESH", "1")
        assert pricing.auto_refresh_enabled(default=False) is True


class TestLazyPersistedLoad:
    """perf: load_persisted_prices() runs on first pricing use, not at
    import time. Simulate a fresh process by resetting the lazy-load flag."""

    @pytest.fixture(autouse=True)
    def _reset_lazy_flag(self):
        original = pricing._prices_loaded
        pricing._prices_loaded = False
        yield
        pricing._prices_loaded = original

    def test_calculate_cost_triggers_lazy_load_of_persisted_file(self, tmp_path, monkeypatch):
        path = tmp_path / "prices.json"
        path.write_text(json.dumps({
            "rates": {"lazy-model": {"in": 5.0, "out": 10.0}},
        }), encoding="utf-8")
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(path))

        assert "lazy-model" not in pricing.RATES  # not loaded yet
        cost = pricing.calculate_cost("lazy-model", tokens_in=1_000_000, tokens_out=0)
        assert cost == pytest.approx(5.0)  # first call triggered the load

    def test_direct_rates_consumers_see_persisted_prices_via_ensure(self, tmp_path, monkeypatch):
        """Consumers that read RATES/PRICES_META directly (CLI table,
        export_feed) must call ensure_prices_loaded() first to see a
        persisted refresh, since they don't go through calculate_cost/
        resolve_model/is_known_model themselves."""
        path = tmp_path / "prices.json"
        path.write_text(json.dumps({
            "version": "2099-01-01",
            "rates": {"direct-consumer-model": {"in": 3.0, "out": 6.0}},
        }), encoding="utf-8")
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(path))

        assert "direct-consumer-model" not in pricing.RATES
        pricing.ensure_prices_loaded()
        assert "direct-consumer-model" in pricing.RATES
        assert pricing.PRICES_META["version"] == "2099-01-01"

    def test_ensure_prices_loaded_only_applies_the_file_once(self, tmp_path, monkeypatch):
        path = tmp_path / "prices.json"
        path.write_text(json.dumps({
            "rates": {"once-model": {"in": 1.0, "out": 2.0}},
        }), encoding="utf-8")
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(path))

        calls = {"n": 0}
        real_load = pricing.load_persisted_prices

        def counting_load():
            calls["n"] += 1
            return real_load()

        monkeypatch.setattr(pricing, "load_persisted_prices", counting_load)
        pricing.ensure_prices_loaded()
        pricing.ensure_prices_loaded()
        pricing.is_known_model("once-model")
        assert calls["n"] == 1
