"""Regression tests for judge-cost estimation (promptry.assertions._judge_cost_details)."""
from __future__ import annotations

import promptry.projectconfig as projectconfig
from promptry.assertions import _judge_cost_details


def _set_judge_model(monkeypatch, model):
    cfg = {"judge": {"model": model} if model else {}}
    monkeypatch.setattr(projectconfig, "load_project_config", lambda: cfg)


class TestJudgeCost:
    def test_priced_when_model_configured(self, monkeypatch):
        _set_judge_model(monkeypatch, "gpt-4o-mini")
        d = _judge_cost_details("grade this response " * 50, '{"score": 0.9}')
        assert d["judge_model"] == "gpt-4o-mini"
        assert d["judge_tokens_in"] > 0 and d["judge_tokens_out"] > 0
        assert d["judge_cost"] is not None and d["judge_cost"] > 0
        assert d["judge_cost_estimated"] is True

    def test_no_cost_without_judge_model(self, monkeypatch):
        _set_judge_model(monkeypatch, None)
        d = _judge_cost_details("x" * 100, "y" * 20)
        assert d["judge_model"] is None
        assert d["judge_cost"] is None

    def test_no_cost_for_unpriced_model(self, monkeypatch):
        _set_judge_model(monkeypatch, "some-unknown-model-xyz")
        d = _judge_cost_details("x" * 100, "y" * 20)
        # tokens still estimated, but no rate -> cost is None
        assert d["judge_tokens_in"] > 0
        assert d["judge_cost"] is None
