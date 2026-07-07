"""Tests for the unified project-config loader (promptry.projectconfig).

promptry historically had two disjoint config files:
  * ``promptry.toml``        (config.py:   [storage] [tracking] [model] [monitor])
  * ``.promptry/config.toml``(projectconfig.py: [dashboard] [judge] [slo] [[models]] [pricing])

Task 2 unifies them: ``promptry.toml`` is the one canonical file carrying ALL
sections, ``~/.promptry/config.toml`` stays as a user-level fallback, and the
legacy ``.promptry/config.toml`` is still merged for back-compat (the project
``promptry.toml`` wins on conflicts). ``load_project_config()`` keeps its dict
return shape but becomes a cached view with ``reset_project_config()``.
"""
from __future__ import annotations

import pytest

import promptry.projectconfig as projectconfig
from promptry.projectconfig import (
    load_project_config,
    reset_project_config,
    save_project_config,
    config_path,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Every test runs in an empty cwd with an empty fake HOME so no stray
    real config leaks in, and with a clean loader cache."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)
    reset_project_config()
    yield
    reset_project_config()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestDefaults:
    def test_defaults_when_no_files(self):
        data = load_project_config()
        assert data["dashboard"]["default_days"] == 14
        assert data["judge"] == {}
        assert data["pricing"] == {}
        assert data["slo"] == {}
        # falls back to the built-in default model list
        assert isinstance(data["models"], list) and data["models"]


class TestCanonicalFile:
    def test_promptry_toml_provides_judge_model(self, tmp_path):
        """The unified promptry.toml carries the [judge] block, and
        load_project_config()["judge"]["model"] reflects it (Task 3 consumes this)."""
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "gpt-4o-mini"\n')
        data = load_project_config()
        assert data["judge"]["model"] == "gpt-4o-mini"

    def test_promptry_toml_carries_all_project_sections(self, tmp_path):
        _write(
            tmp_path / "promptry.toml",
            '[dashboard]\ndefault_days = 30\n\n'
            '[judge]\nmodel = "claude-haiku-4-5"\nmax_prompt_chars = 4000\n\n'
            '[slo]\nmax_latency_ms = 8000\n\n'
            '[[models]]\nid = "my-model"\nprovider = "openai"\n\n'
            '[pricing.my-model]\nin = 1.0\nout = 2.0\n',
        )
        data = load_project_config()
        assert data["dashboard"]["default_days"] == 30
        assert data["judge"]["model"] == "claude-haiku-4-5"
        assert data["judge"]["max_prompt_chars"] == 4000
        assert data["slo"]["max_latency_ms"] == 8000
        assert data["models"] == [{"id": "my-model", "provider": "openai"}]
        assert data["pricing"]["my-model"] == {"in": 1.0, "out": 2.0}


class TestLegacyBackCompat:
    def test_legacy_dotpromptry_still_honored(self, tmp_path):
        """A repo that only has the legacy .promptry/config.toml keeps working."""
        _write(tmp_path / ".promptry" / "config.toml", '[judge]\nmodel = "legacy-model"\n')
        data = load_project_config()
        assert data["judge"]["model"] == "legacy-model"

    def test_project_promptry_toml_wins_over_legacy(self, tmp_path):
        """When both files exist, promptry.toml wins on conflicting keys."""
        _write(tmp_path / ".promptry" / "config.toml",
               '[judge]\nmodel = "legacy-model"\nmax_prompt_chars = 1000\n')
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "canonical-model"\n')
        data = load_project_config()
        # canonical wins on the conflicting key ...
        assert data["judge"]["model"] == "canonical-model"
        # ... but non-conflicting legacy keys are still merged in
        assert data["judge"]["max_prompt_chars"] == 1000

    def test_user_home_fallback_lowest_precedence(self, tmp_path):
        home = tmp_path / "home"
        _write(home / ".promptry" / "config.toml", '[judge]\nmodel = "user-model"\n')
        # no project files -> user fallback provides the value
        assert load_project_config()["judge"]["model"] == "user-model"
        # project file overrides the user fallback
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "project-model"\n')
        reset_project_config()
        assert load_project_config()["judge"]["model"] == "project-model"


class TestCaching:
    def test_repeated_calls_do_not_reread_file(self, tmp_path, monkeypatch):
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "cached"\n')
        load_project_config()  # warm the cache

        calls = {"n": 0}
        real_load = projectconfig.tomllib.load

        def counting_load(f):
            calls["n"] += 1
            return real_load(f)

        monkeypatch.setattr(projectconfig.tomllib, "load", counting_load)
        for _ in range(5):
            assert load_project_config()["judge"]["model"] == "cached"
        assert calls["n"] == 0  # served from cache, no re-parse

    def test_reset_clears_cache(self, tmp_path):
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "first"\n')
        assert load_project_config()["judge"]["model"] == "first"
        # rewrite then reset -> new value observed
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "second"\n')
        reset_project_config()
        assert load_project_config()["judge"]["model"] == "second"

    def test_mtime_change_invalidates_cache(self, tmp_path):
        f = tmp_path / "promptry.toml"
        _write(f, '[judge]\nmodel = "v1"\n')
        assert load_project_config()["judge"]["model"] == "v1"
        # bump mtime forward so the signature changes on rewrite
        import os
        _write(f, '[judge]\nmodel = "v2"\n')
        future = f.stat().st_mtime + 10
        os.utime(f, (future, future))
        assert load_project_config()["judge"]["model"] == "v2"

    def test_returned_dict_is_not_shared_mutable_cache(self, tmp_path):
        """Mutating the returned dict must not poison the cache (dashboard's
        update_project_config mutates before saving)."""
        _write(tmp_path / "promptry.toml", '[judge]\nmodel = "orig"\n')
        d = load_project_config()
        d["judge"]["model"] = "mutated"
        assert load_project_config()["judge"]["model"] == "orig"


class TestSaveRoundTrip:
    def test_save_then_load_reflects_change(self, tmp_path):
        data = load_project_config()
        data["judge"]["model"] = "written-model"
        save_project_config(data)
        # save invalidates the cache, so the next load sees the new value
        assert load_project_config()["judge"]["model"] == "written-model"
        assert config_path().is_file()
