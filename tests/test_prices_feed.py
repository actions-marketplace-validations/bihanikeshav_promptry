"""Tests for the opt-in price feed: bundled snapshot + refresh, no hosted service."""
from __future__ import annotations

import json

import pytest

from promptry import pricing


@pytest.fixture(autouse=True)
def _restore_pricing_globals():
    """The feed functions mutate module-global RATES/REROUTES/PRICES_META.
    Snapshot and restore so tests stay independent."""
    rates = {k: dict(v) for k, v in pricing.RATES.items()}
    reroutes = dict(pricing.REROUTES)
    meta = dict(pricing.PRICES_META)
    yield
    pricing.RATES.clear()
    pricing.RATES.update(rates)
    pricing.REROUTES.clear()
    pricing.REROUTES.update(reroutes)
    pricing.PRICES_META.clear()
    pricing.PRICES_META.update(meta)


class TestExportFeed:
    def test_export_is_json_serializable_and_complete(self):
        feed = pricing.export_feed()
        assert set(feed) >= {"version", "rates", "reroutes"}
        assert "gpt-4o" in feed["rates"]
        # reroutes serialize as [effective_date, replacement] lists
        assert feed["reroutes"]["grok-3"] == ["2026-05-15", "grok-4.3"]
        json.dumps(feed)  # must not raise

    def test_export_round_trips_through_apply(self):
        feed = pricing.export_feed()
        # applying the exported feed should be a no-op on the known models
        before = {k: dict(v) for k, v in pricing.RATES.items()}
        pricing.apply_feed(feed)
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
