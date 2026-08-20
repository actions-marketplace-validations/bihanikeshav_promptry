"""Budget enforcement: opt-in hard ceiling at the completion seam."""
import pytest

from promptry.storage.sqlite import SQLiteStorage
from promptry import budget
from promptry import llm


@pytest.fixture
def storage(tmp_path):
    s = SQLiteStorage(tmp_path / "t.db")
    yield s
    s.close()


def _breach_global(storage, limit=0.01, spend=0.05):
    storage.save_budget("global", "monthly", limit)
    storage.record_invocation("p", metadata={"cost": spend, "model": "gpt-4o"})


class TestEnforce:
    def test_noop_when_disabled(self, storage, monkeypatch):
        _breach_global(storage)
        monkeypatch.delenv("PROMPTRY_ENFORCE_BUDGETS", raising=False)
        budget.enforce_budgets(storage=storage)  # must not raise

    def test_raises_when_breached(self, storage, monkeypatch):
        _breach_global(storage)
        monkeypatch.setenv("PROMPTRY_ENFORCE_BUDGETS", "1")
        with pytest.raises(budget.BudgetExceededError):
            budget.enforce_budgets(storage=storage)

    def test_under_limit_passes(self, storage, monkeypatch):
        storage.save_budget("global", "monthly", 100.0)
        storage.record_invocation("p", metadata={"cost": 0.05, "model": "gpt-4o"})
        monkeypatch.setenv("PROMPTRY_ENFORCE_BUDGETS", "1")
        budget.enforce_budgets(storage=storage)  # not breached -> no raise

    def test_module_scope_matching(self, storage, monkeypatch):
        storage.save_budget("module", "monthly", 0.01, target="mod")
        storage.record_invocation("mod.x", metadata={"cost": 0.05, "model": "gpt-4o"})
        monkeypatch.setenv("PROMPTRY_ENFORCE_BUDGETS", "1")
        # a module budget doesn't block an unscoped/global call...
        budget.enforce_budgets(prompt_name=None, storage=storage)
        # ...but does block a call scoped to that module
        with pytest.raises(budget.BudgetExceededError):
            budget.enforce_budgets(prompt_name="mod.x", storage=storage)

    def test_fail_open_without_budget_support(self, monkeypatch):
        class _NoBudgets:
            def supports(self, cap):
                return False
        monkeypatch.setenv("PROMPTRY_ENFORCE_BUDGETS", "1")
        budget.enforce_budgets(storage=_NoBudgets())  # no raise


class TestCompletionSeam:
    def test_completion_blocks_before_provider_call(self, storage, monkeypatch):
        _breach_global(storage)
        monkeypatch.setenv("PROMPTRY_ENFORCE_BUDGETS", "1")
        # completion() resolves storage via promptry.storage.get_storage
        monkeypatch.setattr("promptry.storage.get_storage", lambda: storage)
        with pytest.raises(budget.BudgetExceededError):
            llm.completion("gpt-4o", [{"role": "user", "content": "hi"}])
