"""Tests for the promptry.llm gateway + judge auto-config.

litellm is monkeypatched so nothing hits the network. We verify:
  - complete() extracts the text content and passes kwargs through to litellm
  - get_default_judge() auto-builds a judge from [judge].model in config
  - an explicit set_judge() callable takes precedence over config
  - get_default_judge() returns None when nothing is configurable
  - assertions.get_judge() falls back to the gateway's default judge
"""
from __future__ import annotations

import types

import pytest

from promptry import llm


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


@pytest.fixture
def fake_litellm(monkeypatch):
    """Install a fake litellm module whose completion records its call args."""
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _FakeResp("hello from model")

    fake = types.SimpleNamespace(completion=completion)
    import sys
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return calls


@pytest.fixture(autouse=True)
def _clear_judge():
    """Ensure the global assertions judge is clean around each test."""
    import promptry.assertions as _mod
    saved = _mod._judge
    _mod._judge = None
    yield
    _mod._judge = saved


class TestComplete:
    def test_extracts_text_content(self, fake_litellm):
        out = llm.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}])
        assert out == "hello from model"

    def test_passes_kwargs_through(self, fake_litellm):
        llm.complete(
            "gpt-4o-mini",
            [{"role": "user", "content": "hi"}],
            temperature=0.3,
            max_tokens=50,
        )
        assert len(fake_litellm) == 1
        sent = fake_litellm[0]
        assert sent["model"] == "gpt-4o-mini"
        assert sent["messages"] == [{"role": "user", "content": "hi"}]
        assert sent["temperature"] == 0.3
        assert sent["max_tokens"] == 50

    def test_empty_content_is_empty_string(self, fake_litellm, monkeypatch):
        import sys
        fake = types.SimpleNamespace(completion=lambda **kw: _FakeResp(None))
        monkeypatch.setitem(sys.modules, "litellm", fake)
        assert llm.complete("m", [{"role": "user", "content": "x"}]) == ""

    def test_missing_litellm_raises_helpful(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "litellm":
                raise ImportError("no litellm")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="pip install"):
            llm.complete("m", [{"role": "user", "content": "x"}])


class TestGetDefaultJudge:
    def test_auto_builds_from_config(self, fake_litellm, monkeypatch):
        monkeypatch.setattr(
            llm, "load_project_config", lambda: {"judge": {"model": "gpt-4o-mini"}}
        )
        judge = llm.get_default_judge()
        assert judge is not None
        assert callable(judge)
        # calling the judge routes through complete -> litellm
        assert judge("grade this") == "hello from model"
        assert fake_litellm[0]["model"] == "gpt-4o-mini"
        assert fake_litellm[0]["messages"] == [
            {"role": "user", "content": "grade this"}
        ]

    def test_explicit_set_judge_takes_precedence(self, monkeypatch):
        monkeypatch.setattr(
            llm, "load_project_config", lambda: {"judge": {"model": "gpt-4o-mini"}}
        )
        import promptry.assertions as assertions

        def my_judge(prompt):
            return "explicit result"

        assertions.set_judge(my_judge)
        try:
            judge = llm.get_default_judge()
            assert judge is my_judge
        finally:
            assertions._judge = None

    def test_none_when_nothing_configured(self, monkeypatch):
        monkeypatch.setattr(llm, "load_project_config", lambda: {"judge": {}})
        assert llm.get_default_judge() is None

    def test_none_when_config_raises(self, monkeypatch):
        def boom():
            raise RuntimeError("config broken")

        monkeypatch.setattr(llm, "load_project_config", boom)
        assert llm.get_default_judge() is None


class TestAssertionsGetJudgeFallback:
    def test_get_judge_falls_back_to_default(self, fake_litellm, monkeypatch):
        import promptry.assertions as assertions

        monkeypatch.setattr(
            llm, "load_project_config", lambda: {"judge": {"model": "cfg-model"}}
        )
        judge = assertions.get_judge()
        assert judge is not None
        assert judge("x") == "hello from model"
        assert fake_litellm[0]["model"] == "cfg-model"

    def test_get_judge_prefers_explicit(self, monkeypatch):
        import promptry.assertions as assertions

        monkeypatch.setattr(
            llm, "load_project_config", lambda: {"judge": {"model": "cfg-model"}}
        )

        def my_judge(prompt):
            return "explicit"

        assertions.set_judge(my_judge)
        try:
            assert assertions.get_judge() is my_judge
        finally:
            assertions._judge = None

    def test_get_judge_none_when_unconfigured(self, monkeypatch):
        import promptry.assertions as assertions

        monkeypatch.setattr(llm, "load_project_config", lambda: {"judge": {}})
        assert assertions.get_judge() is None
