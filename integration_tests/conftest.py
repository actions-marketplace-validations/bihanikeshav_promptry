"""Shared fixtures for the RAG integration tests."""
from __future__ import annotations

import pytest

from integration_tests.rag_pipeline import (
    build_store,
    ollama_available,
    rag_pipeline,
    ollama_judge_factory,
    SYSTEM_PROMPT_V1,
)


def pytest_collection_modifyitems(config, items):
    """Auto-mark everything in this directory as @pytest.mark.integration."""
    for item in items:
        if "integration_tests" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def ollama_guard():
    """Skip entire integration suite if Ollama isn't reachable."""
    if not ollama_available():
        pytest.skip("Ollama not running on localhost:11434 — skipping integration tests.")


@pytest.fixture(scope="session")
def vector_store(ollama_guard):
    return build_store()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Give each test its own promptry SQLite store.

    NB: we also reset the prompt registry. `reset_storage()` alone is
    insufficient — the module-global PromptRegistry caches a reference
    to the old (now-closed) storage and would fail subsequent track()
    calls with "Cannot operate on a closed database". That's a latent
    bug in promptry's own state management; worked around here.
    """
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "promptry.db"))
    from promptry.config import reset_config
    from promptry.storage import reset_storage
    from promptry.registry import reset_registry
    from promptry.evaluator import clear_suites
    reset_registry()
    reset_storage()
    reset_config()
    clear_suites()
    yield tmp_path
    reset_registry()
    reset_storage()
    reset_config()
    clear_suites()


@pytest.fixture
def rag(vector_store):
    """Callable: rag(question, **kwargs) -> RAGResponse."""
    def _call(question, **kw):
        return rag_pipeline(question, vector_store, **kw)
    return _call


@pytest.fixture
def ollama_judge():
    """Return a judge callable; don't auto-install so tests opt in."""
    return ollama_judge_factory()


@pytest.fixture
def install_ollama_judge(ollama_judge):
    """Install the Ollama judge globally and uninstall after the test."""
    from promptry.assertions import set_judge
    set_judge(ollama_judge)
    yield ollama_judge
    set_judge(None)  # type: ignore[arg-type]
