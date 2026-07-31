import pytest
from promptry.storage import Storage
from promptry.registry import PromptRegistry, reset_registry
from promptry.config import reset_config
from promptry.evaluator import clear_suites


@pytest.fixture
def storage(tmp_path):
    db = Storage(db_path=tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def registry(storage):
    return PromptRegistry(storage=storage)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    # Dashboard lifespan would otherwise pull the public price feed during tests.
    monkeypatch.setenv("PROMPTRY_PRICES_AUTO_REFRESH", "0")
    reset_registry()
    reset_config()
    clear_suites()
    yield
    reset_registry()
    reset_config()
    clear_suites()

