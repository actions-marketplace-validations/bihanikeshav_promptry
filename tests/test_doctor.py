import pytest
from typer.testing import CliRunner
from promptry.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
    from promptry.config import reset_config
    reset_config()
    yield
    reset_config()


class TestDoctor:

    def test_doctor_runs(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0

    def test_doctor_reports_all_checks(self):
        # One invocation covers every section + the summary line. (Was six
        # separate `doctor` invocations each grepping one substring.)
        result = runner.invoke(app, ["doctor"])
        for marker in (
            "Python version",
            "Storage writable",
            "sentence-transformers",
            "Dashboard",
            "ok,",
            "warnings",
        ):
            assert marker in result.output, f"missing doctor check: {marker!r}"
