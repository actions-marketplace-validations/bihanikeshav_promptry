"""Tests for `promptry new suite` -- the non-interactive scaffold wizard."""
import pytest
from typer.testing import CliRunner

from promptry.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
    from promptry.config import reset_config
    from promptry.storage import reset_storage
    reset_storage()
    reset_config()
    yield
    reset_storage()
    reset_config()


@pytest.fixture(autouse=True)
def clear_registry():
    import sys
    from promptry.evaluator import clear_suites
    clear_suites()
    yield
    clear_suites()
    sys.modules.pop("wiznewmod", None)
    sys.modules.pop("evals", None)


class TestNewSuiteYaml:

    def test_generates_yaml_and_loads(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "wiznewmod.py").write_text(
            "def my_pipeline(x):\n    return f'X is {x}'\n", encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "my-suite",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "What is X?::contains::X is",
        ])
        assert result.exit_code == 0, result.output
        yaml_path = tmp_path / "evals.yaml"
        assert yaml_path.exists()

        from promptry.yaml_suites import load_yaml_suites
        names = load_yaml_suites(yaml_path)
        assert "my-suite" in names

    def test_prints_run_command(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "my-suite",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
        ])
        assert result.exit_code == 0, result.output
        assert "promptry run my-suite" in result.output

    def test_model_prompt_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "model-suite",
            "--yaml",
            "--model", "gpt-4o-mini",
            "--prompt", "Answer: {input}",
            "--case", "hi::contains::hello",
        ])
        assert result.exit_code == 0, result.output
        content = (tmp_path / "evals.yaml").read_text(encoding="utf-8")
        assert "model: gpt-4o-mini" in content
        assert "Answer: {input}" in content

        from promptry.yaml_suites import load_yaml_suites
        names = load_yaml_suites(tmp_path / "evals.yaml")
        assert "model-suite" in names

    def test_multiple_assertions_same_input_merge_into_one_case(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "merged",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
            "--case", "hi::not_contains::bye",
        ])
        assert result.exit_code == 0, result.output
        import yaml as _yaml
        doc = _yaml.safe_load((tmp_path / "evals.yaml").read_text(encoding="utf-8"))
        suite = next(s for s in doc["suites"] if s["name"] == "merged")
        assert len(suite["cases"]) == 1
        assert len(suite["cases"][0]["expect"]) == 2

    def test_append_to_existing_evals_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "wiznewmod.py").write_text(
            "def my_pipeline(x):\n    return f'echo {x}'\n", encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        yaml_path = tmp_path / "evals.yaml"
        yaml_path.write_text(
            "suites:\n"
            "  - name: existing-suite\n"
            "    pipeline: wiznewmod:my_pipeline\n"
            "    cases:\n"
            "      - input: hi\n"
            "        expect:\n"
            "          - contains: hello\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "new-suite",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hey::contains::yo",
        ])
        assert result.exit_code == 0, result.output

        from promptry.yaml_suites import load_yaml_suites
        names = load_yaml_suites(yaml_path)
        assert set(names) == {"existing-suite", "new-suite"}

    def test_append_to_commented_only_evals_yaml(self, tmp_path, monkeypatch):
        """A freshly `init`-scaffolded evals.yaml is all comments -- appending
        should behave like creating a fresh file, not error out."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        assert (tmp_path / "evals.yaml").exists()
        (tmp_path / "wiznewmod.py").write_text(
            "def my_pipeline(x):\n    return f'echo {x}'\n", encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))

        result = runner.invoke(app, [
            "new", "suite",
            "--name", "fresh-suite",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hey::contains::yo",
        ])
        assert result.exit_code == 0, result.output

        from promptry.yaml_suites import load_yaml_suites
        names = load_yaml_suites(tmp_path / "evals.yaml")
        assert names == ["fresh-suite"]

    def test_run_hint_uses_module_flag_when_evals_py_exists(self, tmp_path, monkeypatch):
        """After `init` both evals.py and evals.yaml exist; default discovery
        imports evals.py, so the printed run command must pass --module."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        assert (tmp_path / "evals.py").exists()

        result = runner.invoke(app, [
            "new", "suite",
            "--name", "hinted",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
        ])
        assert result.exit_code == 0, result.output
        assert "promptry run hinted --module evals.yaml" in result.output

    def test_run_hint_bare_when_no_evals_py(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "bare",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
        ])
        assert result.exit_code == 0, result.output
        assert "promptry run bare" in result.output
        assert "--module" not in result.output

    def test_run_hint_custom_yaml_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "custom-out",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
            "--output", "custom.yaml",
        ])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "custom.yaml").exists()
        assert "promptry run custom-out --module custom.yaml" in result.output

    def test_run_hint_custom_python_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "custom-py",
            "--python",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
            "--output", "mysuites.py",
        ])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "mysuites.py").exists()
        assert "promptry run custom-py --module mysuites" in result.output

    def test_refuses_to_clobber_invalid_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        yaml_path = tmp_path / "evals.yaml"
        broken = "suites: [unclosed\n  - ::: {{{\n"
        yaml_path.write_text(broken, encoding="utf-8")

        result = runner.invoke(app, [
            "new", "suite",
            "--name", "nope",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
        ])
        assert result.exit_code != 0
        assert "--output" in result.output
        # file untouched
        assert yaml_path.read_text(encoding="utf-8") == broken

    def test_refuses_to_clobber_yaml_without_suites_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        yaml_path = tmp_path / "evals.yaml"
        other = "someone_elses_config: true\nvalues: [1, 2, 3]\n"
        yaml_path.write_text(other, encoding="utf-8")

        result = runner.invoke(app, [
            "new", "suite",
            "--name", "nope",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::hello",
        ])
        assert result.exit_code != 0
        assert "suites" in result.output
        # file untouched
        assert yaml_path.read_text(encoding="utf-8") == other


class TestNewSuitePython:

    def test_generates_python_and_imports(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "wiznewmod.py").write_text(
            "def my_pipeline(x):\n    return f'echo {x}'\n", encoding="utf-8",
        )
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "py-suite",
            "--python",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::echo",
        ])
        assert result.exit_code == 0, result.output
        evals_path = tmp_path / "evals.py"
        assert evals_path.exists()

        import sys
        sys.path.insert(0, str(tmp_path))
        try:
            from promptry.evaluator import get_suite
            import importlib
            importlib.import_module("evals")
            assert get_suite("py-suite") is not None
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("evals", None)
            sys.modules.pop("wiznewmod", None)

    def test_appends_to_existing_evals_py(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "wiznewmod.py").write_text(
            "def my_pipeline(x):\n    return f'echo {x}'\n", encoding="utf-8",
        )
        evals_path = tmp_path / "evals.py"
        evals_path.write_text(
            "from promptry import suite\n\n"
            "@suite('already-here')\n"
            "def test_already():\n"
            "    pass\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "appended-suite",
            "--python",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "hi::contains::echo",
        ])
        assert result.exit_code == 0, result.output
        content = evals_path.read_text(encoding="utf-8")
        assert "already-here" in content
        assert "appended-suite" in content


class TestNewSuiteValidation:

    def test_invalid_case_syntax_missing_parts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "bad",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "just-input-no-separator",
        ])
        assert result.exit_code != 0
        assert "--case" in result.output

    def test_invalid_case_unknown_assertion(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "bad",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
            "--case", "input::not_a_real_assertion::value",
        ])
        assert result.exit_code != 0
        assert "assertion" in result.output.lower()

    def test_missing_pipeline_and_model_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "bad",
            "--yaml",
            "--case", "input::contains::value",
        ])
        assert result.exit_code != 0

    def test_model_without_prompt_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "bad",
            "--yaml",
            "--model", "gpt-4o-mini",
            "--case", "input::contains::value",
        ])
        assert result.exit_code != 0

    def test_no_cases_at_all_non_interactive_errors(self, tmp_path, monkeypatch):
        """Providing every other flag but zero --case should not hang waiting
        on stdin in a non-interactive invocation with no input."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [
            "new", "suite",
            "--name", "bad",
            "--yaml",
            "--pipeline", "wiznewmod:my_pipeline",
        ], input="")
        # No cases collected interactively (blank input) -> should error out
        # cleanly rather than crash.
        assert result.exit_code != 0
