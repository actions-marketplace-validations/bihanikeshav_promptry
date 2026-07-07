"""Conformance test for the BaseStorage interface.

For every public BaseStorage method, `storage.supports(name)` must
accurately predict whether calling it raises NotImplementedError (the
BaseStorage default) or actually works (an override).

Parameterized over every concrete backend so a backend that only
partially implements the interface can't silently pass -- the dashboard
uses `supports()` to decide what to show, so it has to be honest.
"""
from __future__ import annotations

import pytest

from promptry.config import reset_config
from promptry.storage.base import BaseStorage
from promptry.storage.sqlite import SQLiteStorage
from promptry.storage.remote import RemoteStorage


def _public_methods(cls) -> list[str]:
    names = []
    for name, member in vars(cls).items():
        if name.startswith("_"):
            continue
        if not callable(member):
            continue
        if name == "supports":
            continue
        names.append(name)
    return sorted(names)


BASE_METHODS = _public_methods(BaseStorage)


@pytest.fixture
def sqlite_storage(tmp_path):
    storage = SQLiteStorage(db_path=tmp_path / "conformance.db")
    yield storage
    storage.close()


@pytest.fixture
def remote_storage(tmp_path, monkeypatch):
    # Dummy, unreachable endpoint -- the test never triggers a flush/emit
    # (no writes happen here), so no network call is made. RemoteStorage
    # always builds its own local SQLiteStorage() from the active config,
    # so point that at a throwaway db via the same env var other
    # RemoteStorage tests use (see test_remote_storage.py).
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "conformance_remote.db"))
    reset_config()
    storage = RemoteStorage(endpoint="http://127.0.0.1:1/unused", api_key="")
    yield storage
    storage._running = False  # skip the flush-loop join dance; no events were queued
    storage._local.close()
    reset_config()


@pytest.fixture(params=["sqlite", "remote"])
def storage(request, sqlite_storage, remote_storage):
    if request.param == "sqlite":
        return sqlite_storage
    return remote_storage


def test_base_has_expected_optional_methods():
    """Sanity check the interface actually grew the promoted methods."""
    expected = {
        "set_prompt_env", "get_runs_for_prompt", "prune_prompt_versions",
        "list_invocations", "get_invocation", "get_invocation_models",
        "count_invocations", "save_feedback", "list_feedback",
        "get_feedback_stats", "bisect_regression", "save_budget",
        "delete_budget", "list_budgets", "get_budget_status",
        "add_golden_example", "list_golden_examples", "delete_golden_example",
    }
    assert expected.issubset(set(BASE_METHODS))


@pytest.mark.parametrize("method_name", BASE_METHODS)
def test_supports_matches_override_identity(storage, method_name):
    """supports() must agree with whether the concrete class overrides the
    method vs. inheriting BaseStorage's own attribute."""
    base_attr = getattr(BaseStorage, method_name)
    concrete_attr = getattr(type(storage), method_name, None)
    is_overridden = concrete_attr is not base_attr
    assert storage.supports(method_name) is is_overridden


def test_sqlite_supports_everything(sqlite_storage):
    """SQLiteStorage is the reference implementation -- every method should
    be supported."""
    for name in BASE_METHODS:
        assert sqlite_storage.supports(name), f"SQLiteStorage should support {name}"


def test_remote_supports_everything(remote_storage):
    """RemoteStorage always keeps a local SQLite store, so it should proxy
    every capability rather than silently dropping ones added after its
    dual-write methods were written."""
    for name in BASE_METHODS:
        assert remote_storage.supports(name), f"RemoteStorage should support {name}"


def test_unsupported_method_raises_not_implemented():
    """A minimal fake backend that only implements the required abstract
    methods must raise NotImplementedError for the optional, promoted ones,
    and supports() must say so up front."""

    class BareStorage(BaseStorage):
        def save_prompt(self, name, content, content_hash, metadata=None, force=False):
            raise NotImplementedError

        def get_prompt(self, name, version=None):
            raise NotImplementedError

        def get_prompt_by_tag(self, name, tag):
            raise NotImplementedError

        def list_prompts(self, name=None, offset=0, limit=100):
            raise NotImplementedError

        def tag_prompt(self, prompt_id, tag):
            raise NotImplementedError

        def get_tags(self, prompt_id):
            raise NotImplementedError

        def save_eval_run(self, suite_name, prompt_name=None, prompt_version=None,
                          model_version=None, overall_pass=True, overall_score=None):
            raise NotImplementedError

        def save_eval_result(self, run_id, test_name, assertion_type, passed,
                             score=None, details=None, latency_ms=None):
            raise NotImplementedError

        def get_eval_runs(self, suite_name, offset=0, limit=50):
            raise NotImplementedError

        def get_eval_runs_batch(self, suite_names, limit_per_suite=20):
            raise NotImplementedError

        def get_eval_results(self, run_id):
            raise NotImplementedError

        def get_eval_results_batch(self, run_ids):
            raise NotImplementedError

        def get_score_history(self, suite_name, limit=30):
            raise NotImplementedError

        def get_runs_by_model(self, suite_name, model_version, limit=200):
            raise NotImplementedError

        def get_model_versions(self, suite_name):
            raise NotImplementedError

        def list_suite_names(self):
            raise NotImplementedError

        def get_eval_run_by_id(self, run_id):
            raise NotImplementedError

        def get_cost_data(self, days=7, name=None, model=None):
            raise NotImplementedError

        def get_model_cost_summary(self, model, days=30):
            raise NotImplementedError

        def list_prompt_summaries(self, offset=0, limit=200):
            raise NotImplementedError

        def get_invocation_stats(self, name, days=30):
            raise NotImplementedError

        def record_invocation(self, prompt_name, metadata=None, prompt_version=None):
            raise NotImplementedError

        def save_dataset(self, name, items, metadata=None):
            raise NotImplementedError

        def get_dataset(self, name, version=None):
            raise NotImplementedError

        def list_datasets(self):
            raise NotImplementedError

        def save_vote(self, prompt_name, response, score, prompt_version=None,
                     message=None, metadata=None):
            raise NotImplementedError

        def get_votes(self, prompt_name=None, days=30, offset=0, limit=200):
            raise NotImplementedError

        def get_vote_stats(self, prompt_name=None, days=30):
            raise NotImplementedError

        def close(self):
            pass

    bare = BareStorage()

    # Representative sample of promoted, never-overridden methods: supports()
    # must say False, and calling them must actually raise.
    for name, args in [
        ("bisect_regression", ("suite",)),
        ("get_budget_status", ()),
        ("list_golden_examples", ("prompt",)),
    ]:
        assert bare.supports(name) is False
        with pytest.raises(NotImplementedError):
            getattr(bare, name)(*args)
