"""Tests for promptry.pricing.build_cost_report (extracted from cli.py's
cost_report_cmd)."""
from __future__ import annotations

import pytest

from promptry.pricing import build_cost_report
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "test.db")
    yield db
    db.close()


class TestBuildCostReport:
    def test_empty_ledger(self, storage):
        data = build_cost_report(storage, days=7)
        assert data["summary"]["total_calls"] == 0
        assert data["unpriced_calls"] == 0

    def test_aggregates_known_model_calls(self, storage):
        storage.record_invocation(
            "my-prompt",
            metadata={"tokens_in": 1000, "tokens_out": 200, "model": "gpt-4o-mini", "cost": 0.01},
        )
        storage.record_invocation(
            "my-prompt",
            metadata={"tokens_in": 500, "tokens_out": 100, "model": "gpt-4o-mini", "cost": 0.005},
        )
        data = build_cost_report(storage, days=7)
        assert data["summary"]["total_calls"] == 2
        assert data["unpriced_calls"] == 0
        assert len(data["by_name"]) == 1
        assert data["by_name"][0]["name"] == "my-prompt"

    def test_flags_unpriced_model_calls(self, storage):
        storage.record_invocation(
            "my-prompt",
            metadata={"tokens_in": 100, "tokens_out": 50, "model": "totally-unpriced-model", "cost": 0.0},
        )
        data = build_cost_report(storage, days=7)
        assert data["unpriced_calls"] == 1

    def test_name_filter_applies_to_unpriced_count(self, storage):
        storage.record_invocation(
            "prompt-a",
            metadata={"tokens_in": 100, "tokens_out": 50, "model": "totally-unpriced-model", "cost": 0.0},
        )
        storage.record_invocation(
            "prompt-b",
            metadata={"tokens_in": 100, "tokens_out": 50, "model": "gpt-4o-mini", "cost": 0.001},
        )
        data = build_cost_report(storage, days=7, name="prompt-b")
        assert data["unpriced_calls"] == 0
        assert data["summary"]["total_calls"] == 1

    def test_model_filter_applies_to_unpriced_count(self, storage):
        storage.record_invocation(
            "my-prompt",
            metadata={"tokens_in": 100, "tokens_out": 50, "model": "totally-unpriced-model", "cost": 0.0},
        )
        storage.record_invocation(
            "my-prompt",
            metadata={"tokens_in": 100, "tokens_out": 50, "model": "gpt-4o-mini", "cost": 0.001},
        )
        data = build_cost_report(storage, days=7, model="gpt-4o-mini")
        assert data["unpriced_calls"] == 0
        assert data["summary"]["total_calls"] == 1

    def test_returns_same_keys_as_storage_get_cost_data_plus_unpriced_calls(self, storage):
        raw = storage.get_cost_data(days=7)
        data = build_cost_report(storage, days=7)
        assert set(raw.keys()) <= set(data.keys())
        assert "unpriced_calls" in data
