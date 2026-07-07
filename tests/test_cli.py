import pytest
from typer.testing import CliRunner
from promptry.cli import app

runner = CliRunner()

try:
    import sentence_transformers  # noqa: F401
    _has_st = True
except ImportError:
    _has_st = False

needs_semantic = pytest.mark.skipif(not _has_st, reason="requires promptry[semantic]")


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


class TestPromptCLI:

    def test_save_from_stdin(self):
        result = runner.invoke(app, ["prompt", "save", "--name", "test"], input="Hello world")
        assert result.exit_code == 0
        assert "Saved" in result.output

    def test_save_from_file(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("File prompt content", encoding="utf-8")
        result = runner.invoke(app, ["prompt", "save", str(f), "--name", "test"])
        assert result.exit_code == 0
        assert "Saved" in result.output

    def test_save_empty_fails(self):
        result = runner.invoke(app, ["prompt", "save", "--name", "test"], input="")
        assert result.exit_code == 1

    def test_list(self):
        runner.invoke(app, ["prompt", "save", "--name", "a"], input="Content A")
        runner.invoke(app, ["prompt", "save", "--name", "b"], input="Content B")
        result = runner.invoke(app, ["prompt", "list"])
        assert result.exit_code == 0
        assert "a" in result.output
        assert "b" in result.output

    def test_show(self):
        runner.invoke(app, ["prompt", "save", "--name", "test"], input="Show me")
        result = runner.invoke(app, ["prompt", "show", "test"])
        assert result.exit_code == 0
        assert "Show me" in result.output

    def test_show_not_found(self):
        result = runner.invoke(app, ["prompt", "show", "nonexistent"])
        assert result.exit_code == 1

    def test_diff(self):
        runner.invoke(app, ["prompt", "save", "--name", "test"], input="Line one\nLine two\n")
        runner.invoke(app, ["prompt", "save", "--name", "test"], input="Line one\nLine changed\n")
        result = runner.invoke(app, ["prompt", "diff", "test", "1", "2"])
        assert result.exit_code == 0
        assert "Line two" in result.output
        assert "Line changed" in result.output

    def test_tag(self):
        runner.invoke(app, ["prompt", "save", "--name", "test"], input="Content")
        result = runner.invoke(app, ["prompt", "tag", "test", "1", "prod"])
        assert result.exit_code == 0
        assert "Tagged" in result.output
        assert "prod" in result.output


class TestInitCLI:

    def test_init_creates_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Created" in result.output
        assert (tmp_path / "promptry.toml").exists()
        assert (tmp_path / "evals.py").exists()

    def test_init_does_not_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "promptry.toml").write_text("existing", encoding="utf-8")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output
        # original content preserved
        assert (tmp_path / "promptry.toml").read_text() == "existing"

    def test_init_creates_valid_toml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        with open(tmp_path / "promptry.toml", "rb") as f:
            data = tomllib.load(f)
        assert "storage" in data

    def test_init_evals_contains_rag_and_classification(self, tmp_path, monkeypatch):
        """The generated evals.py should include rag-qa and classification suites."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        content = (tmp_path / "evals.py").read_text(encoding="utf-8")
        assert "rag-qa" in content
        assert "classification" in content

    def test_init_evals_contains_new_suites(self, tmp_path, monkeypatch):
        """The generated evals.py should include chat-quality, extraction, and summarization suites."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        content = (tmp_path / "evals.py").read_text(encoding="utf-8")
        assert "chat-quality" in content
        assert "extraction" in content
        assert "summarization" in content

    def test_init_evals_is_valid_python(self, tmp_path, monkeypatch):
        """The generated evals.py must be valid Python that compiles without errors."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        source = (tmp_path / "evals.py").read_text(encoding="utf-8")
        # compile() will raise SyntaxError if the source is invalid
        compile(source, "evals.py", "exec")

    def test_init_my_pipeline_raises_not_implemented(self, tmp_path, monkeypatch):
        """The scaffolded my_pipeline must raise NotImplementedError instead of
        returning a hardcoded placeholder string that fakes a passing eval."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        monkeypatch.syspath_prepend(str(tmp_path))
        import importlib
        import sys
        sys.modules.pop("evals", None)  # never reuse another test's cached module
        try:
            evals = importlib.import_module("evals")
            with pytest.raises(NotImplementedError):
                evals.my_pipeline("What is machine learning?")
        finally:
            sys.modules.pop("evals", None)

    def test_init_toml_has_commented_judge_block(self, tmp_path, monkeypatch):
        """The unified promptry.toml scaffold should include a commented [judge] block."""
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        content = (tmp_path / "promptry.toml").read_text(encoding="utf-8")
        assert "[judge]" in content
        assert "[models]" in content or "[[models]]" in content
        assert "[pricing" in content

    def test_init_commented_blocks_are_valid_toml_when_uncommented(self, tmp_path, monkeypatch):
        """Uncommenting the scaffold's [judge]/[[models]]/[pricing] reference
        blocks must yield parseable TOML — guards against typos in the
        commented examples users are told to enable."""
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        lines = (tmp_path / "promptry.toml").read_text(encoding="utf-8").splitlines()
        uncommented = []
        for line in lines:
            stripped = line.lstrip()
            # uncomment only "# <toml>" example lines, not prose comments
            if stripped.startswith("# ") and any(
                c in stripped for c in ("=", "[", "]")
            ):
                uncommented.append(stripped[2:])
            elif not stripped.startswith("#"):
                uncommented.append(line)
        data = tomllib.loads("\n".join(uncommented))
        assert "judge" in data
        assert "models" in data
        assert "pricing" in data

    def test_init_next_steps_mentions_api_key_and_doctor(self, tmp_path, monkeypatch):
        """Next-steps output should tell the user how to set an API key and to run doctor."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert "OPENAI_API_KEY" in result.output or "ANTHROPIC_API_KEY" in result.output
        assert "promptry doctor" in result.output


class TestTemplatesCLI:

    def test_templates_list(self):
        result = runner.invoke(app, ["templates", "list"])
        assert result.exit_code == 0
        assert "injection" in result.output.lower() or "jailbreak" in result.output.lower()

    @needs_semantic
    def test_templates_run_custom_func(self, tmp_path, monkeypatch):
        """--func flag should use the specified function name."""
        mod_file = tmp_path / "mymod.py"
        mod_file.write_text(
            "def my_llm(prompt):\n"
            "    return 'I cannot help with that request.'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        result = runner.invoke(
            app, ["templates", "run", "--module", "mymod", "--func", "my_llm", "--category", "prompt_injection"]
        )
        assert result.exit_code in (0, 1)  # runs without crashing
        assert "PASS" in result.output or "FAIL" in result.output

    def test_templates_run_missing_func(self, tmp_path, monkeypatch):
        """Should error when specified function doesn't exist."""
        mod_file = tmp_path / "emptymod.py"
        mod_file.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        result = runner.invoke(
            app, ["templates", "run", "--module", "emptymod", "--func", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "nonexistent" in result.output


class TestCostReport:

    def test_cost_report_no_data(self):
        """cost-report should exit 0 with a helpful message when no data exists."""
        result = runner.invoke(app, ["cost-report"])
        assert result.exit_code == 0
        assert "No prompts with metadata" in result.output or "no" in result.output.lower()

    def test_cost_report_with_data(self, tmp_path, monkeypatch):
        """cost-report should display token/cost aggregates when data exists."""
        import json
        from promptry.storage import Storage

        db_path = tmp_path / "test_cost.db"
        monkeypatch.setenv("PROMPTRY_DB", str(db_path))
        from promptry.config import reset_config
        reset_config()

        storage = Storage(db_path=db_path)
        meta = {"tokens_in": 100, "tokens_out": 50, "cost": 0.001, "model": "gpt-4o"}
        storage.record_invocation("cost-test", metadata=meta)
        storage.close()

        result = runner.invoke(app, ["cost-report", "--days", "30"])
        assert result.exit_code == 0
        assert "cost-test" in result.output

    def test_cost_report_name_filter(self, tmp_path, monkeypatch):
        """cost-report --name should filter to only the specified prompt."""
        from promptry.storage import Storage

        db_path = tmp_path / "test_cost2.db"
        monkeypatch.setenv("PROMPTRY_DB", str(db_path))
        from promptry.config import reset_config
        reset_config()

        storage = Storage(db_path=db_path)
        meta_a = {"tokens_in": 100, "tokens_out": 50, "cost": 0.001, "model": "gpt-4o"}
        meta_b = {"tokens_in": 200, "tokens_out": 75, "cost": 0.002, "model": "gpt-4o"}
        storage.record_invocation("prompt-a", metadata=meta_a)
        storage.record_invocation("prompt-b", metadata=meta_b)
        storage.close()

        result = runner.invoke(app, ["cost-report", "--name", "prompt-a", "--days", "30"])
        assert result.exit_code == 0
        assert "prompt-a" in result.output

    def test_cost_report_model_filter(self, tmp_path, monkeypatch):
        """cost-report --model should filter to matching models."""
        from promptry.storage import Storage

        db_path = tmp_path / "test_cost3.db"
        monkeypatch.setenv("PROMPTRY_DB", str(db_path))
        from promptry.config import reset_config
        reset_config()

        storage = Storage(db_path=db_path)
        meta = {"tokens_in": 100, "tokens_out": 50, "cost": 0.001, "model": "claude-sonnet"}
        storage.record_invocation("model-test", metadata=meta)
        storage.close()

        # Filter for a model that does not match
        result = runner.invoke(app, ["cost-report", "--model", "gpt-4o", "--days", "30"])
        assert result.exit_code == 0
        # Should show no data for gpt-4o since only claude-sonnet was saved
        assert "No prompts with token/cost metadata" in result.output or "model-test" not in result.output

    def test_cost_report_warns_on_unpriced_model(self, tmp_path, monkeypatch):
        """cost-report should call out un-priced models with a warning + tip."""
        from promptry.storage import Storage

        db_path = tmp_path / "test_cost4.db"
        monkeypatch.setenv("PROMPTRY_DB", str(db_path))
        from promptry.config import reset_config
        reset_config()

        storage = Storage(db_path=db_path)
        meta = {"tokens_in": 100, "tokens_out": 50, "cost": 0.0, "model": "totally-unpriced-model-xyz"}
        storage.record_invocation("unpriced-test", metadata=meta)
        storage.close()

        result = runner.invoke(app, ["cost-report", "--days", "30"])
        assert result.exit_code == 0
        assert "un-priced models" in result.output
        assert "promptry prices --check" in result.output


class TestDoctor:

    def test_doctor_shows_version(self):
        """doctor should display the promptry version number."""
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        from promptry import __version__
        assert __version__ in result.output

    def test_doctor_shows_python_version(self):
        """doctor should display the Python version."""
        import sys
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        v = sys.version_info
        assert f"{v.major}.{v.minor}.{v.micro}" in result.output

    def test_doctor_checks_disk_space(self):
        """doctor should report on disk space."""
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Disk space" in result.output

    def test_doctor_summary_breaks_out_fail_count(self):
        """The summary line should report ok/warnings/failed as separate counts."""
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "failed" in result.output

    def test_doctor_exits_nonzero_on_forced_fail(self, monkeypatch):
        """A forced failure (e.g. storage unreachable) should set exit code 1."""
        import promptry.storage as storage_mod

        def _broken_storage():
            raise RuntimeError("storage boom")

        monkeypatch.setattr(storage_mod, "get_storage", _broken_storage)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "1 failed" in result.output

    def test_doctor_checks_provider_api_key(self, monkeypatch):
        """doctor should warn when no known provider API key is set."""
        from promptry.projectconfig import PROVIDER_ENV
        for env in PROVIDER_ENV.values():
            monkeypatch.delenv(env, raising=False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Provider API key" in result.output


class TestRunCLI:

    def _write_evals_module(self, tmp_path, monkeypatch, module_name="evals"):
        mod_file = tmp_path / f"{module_name}.py"
        mod_file.write_text(
            "from promptry.evaluator import suite\n"
            "from promptry.assertions import assert_contains\n\n"
            "@suite(\"smoke\")\n"
            "def test_smoke():\n"
            "    assert_contains(\"hello world\", [\"hello\"])\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        import sys
        for name in list(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)

    def test_run_without_module_defaults_to_evals(self, tmp_path, monkeypatch):
        """Mirrors the dashboard onboarding card's `promptry run <name>` with no --module,
        against a freshly-registered 'evals' module in the project directory."""
        self._write_evals_module(tmp_path, monkeypatch)
        result = runner.invoke(app, ["run", "smoke"])
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output

    def test_run_unknown_suite_lists_available_suites(self, tmp_path, monkeypatch):
        self._write_evals_module(tmp_path, monkeypatch)
        result = runner.invoke(app, ["run", "does-not-exist"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "smoke" in result.output


class TestRunCLIFormat:
    """--format json / --format junit on `promptry run`."""

    def _write_passing_module(self, tmp_path, monkeypatch, module_name="evals_pass"):
        mod_file = tmp_path / f"{module_name}.py"
        mod_file.write_text(
            "from promptry.evaluator import suite\n"
            "from promptry.assertions import assert_contains\n\n"
            "@suite(\"smoke\")\n"
            "def test_smoke():\n"
            "    assert_contains(\"hello world\", [\"hello\"])\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        import sys
        for name in list(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)

    def _write_failing_module(self, tmp_path, monkeypatch, module_name="evals_fail"):
        mod_file = tmp_path / f"{module_name}.py"
        mod_file.write_text(
            "from promptry.evaluator import suite\n"
            "from promptry.assertions import assert_contains\n\n"
            "@suite(\"smoke\")\n"
            "def test_smoke():\n"
            "    assert_contains(\"hello world\", [\"goodbye\"])\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        import sys
        for name in list(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)

    def test_json_stdout_is_pure_json(self, tmp_path, monkeypatch):
        """Nothing but the JSON payload may land on stdout -- a consumer must
        be able to json.loads(result.stdout) directly."""
        import json

        self._write_passing_module(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app, ["run", "smoke", "--module", "evals_pass", "--format", "json"]
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["suite_name"] == "smoke"
        assert data["overall_pass"] is True

    def test_json_matches_to_dict_schema(self, tmp_path, monkeypatch):
        import json

        self._write_passing_module(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app, ["run", "smoke", "--module", "evals_pass", "--format", "json"]
        )
        data = json.loads(result.stdout)
        assert set(data.keys()) == {"suite_name", "overall_pass", "overall_score", "tests"}
        assert data["tests"][0]["test_name"] == "smoke"

    def test_json_failing_suite_still_exits_1(self, tmp_path, monkeypatch):
        import json

        self._write_failing_module(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app, ["run", "smoke", "--module", "evals_fail", "--format", "json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["overall_pass"] is False

    def test_junit_stdout_parses_as_xml(self, tmp_path, monkeypatch):
        import xml.etree.ElementTree as ET

        self._write_passing_module(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app, ["run", "smoke", "--module", "evals_pass", "--format", "junit"]
        )
        assert result.exit_code == 0, result.stderr
        root = ET.fromstring(result.stdout)
        suite = root.find("testsuite")
        assert suite.get("name") == "smoke"
        assert suite.get("tests") == "1"
        assert suite.get("failures") == "0"

    def test_junit_failing_suite_counts_match(self, tmp_path, monkeypatch):
        import xml.etree.ElementTree as ET

        self._write_failing_module(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app, ["run", "smoke", "--module", "evals_fail", "--format", "junit"]
        )
        assert result.exit_code == 1
        root = ET.fromstring(result.stdout)
        suite = root.find("testsuite")
        assert suite.get("tests") == "1"
        assert suite.get("failures") == "1"

    def test_json_output_flag_writes_file_not_stdout(self, tmp_path, monkeypatch):
        import json

        self._write_passing_module(tmp_path, monkeypatch)
        out_path = tmp_path / "report.json"
        result = CliRunner().invoke(
            app,
            ["run", "smoke", "--module", "evals_pass", "--format", "json", "--output", str(out_path)],
        )
        assert result.exit_code == 0, result.stderr
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["suite_name"] == "smoke"
        # stdout must stay empty -- the "written to" note is routed to stderr
        assert result.stdout.strip() == ""
        assert "Report written to" in result.stderr

    def test_invalid_format_rejected(self, tmp_path, monkeypatch):
        self._write_passing_module(tmp_path, monkeypatch)
        result = runner.invoke(app, ["run", "smoke", "--module", "evals_pass", "--format", "yaml"])
        assert result.exit_code == 2
        assert "Invalid --format" in result.output


class TestCompare:

    def test_compare_no_data(self):
        """compare should handle gracefully when no eval data exists."""
        result = runner.invoke(app, ["compare", "nonexistent", "--candidate", "model-a"])
        # Should exit with code 1 (error) because no baseline data exists, not crash
        assert result.exit_code == 1
        assert "Error" in result.output or "No baseline" in result.output or "No runs" in result.output


class TestCompareCLIFormat:
    """--format json / --format junit on `promptry compare`."""

    def _seed(self, tmp_path, monkeypatch):
        from promptry.storage import Storage

        db_path = tmp_path / "test.db"
        monkeypatch.setenv("PROMPTRY_DB", str(db_path))
        from promptry.config import reset_config
        from promptry.storage import reset_storage
        reset_storage()
        reset_config()

        storage = Storage(db_path=db_path)
        for _ in range(3):
            run_id = storage.save_eval_run(
                suite_name="cmp-suite", prompt_name=None, prompt_version=None,
                model_version="model-a", overall_pass=True, overall_score=0.85,
            )
            storage.save_eval_result(
                run_id=run_id, test_name="t1", assertion_type="semantic",
                passed=True, score=0.85, details=None, latency_ms=100.0,
            )
        for _ in range(2):
            run_id = storage.save_eval_run(
                suite_name="cmp-suite", prompt_name=None, prompt_version=None,
                model_version="model-b", overall_pass=True, overall_score=0.90,
            )
            storage.save_eval_result(
                run_id=run_id, test_name="t1", assertion_type="semantic",
                passed=True, score=0.90, details=None, latency_ms=80.0,
            )
        storage.close()

    def test_json_stdout_matches_to_dict(self, tmp_path, monkeypatch):
        import json

        self._seed(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app,
            ["compare", "cmp-suite", "--candidate", "model-b", "--baseline", "model-a", "--format", "json"],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["baseline"]["model_version"] == "model-a"
        assert data["candidate"]["model_version"] == "model-b"

    def test_junit_stdout_parses_as_xml(self, tmp_path, monkeypatch):
        import xml.etree.ElementTree as ET

        self._seed(tmp_path, monkeypatch)
        result = CliRunner().invoke(
            app,
            ["compare", "cmp-suite", "--candidate", "model-b", "--baseline", "model-a", "--format", "junit"],
        )
        assert result.exit_code == 0, result.stderr
        root = ET.fromstring(result.stdout)
        assert root.find("testsuite") is not None


class TestDriftCLIFormat:
    """--format json / --format junit on `promptry drift`."""

    @pytest.fixture(autouse=True)
    def isolated_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
        from promptry.config import reset_config
        from promptry.storage import reset_storage
        reset_storage()
        reset_config()
        yield
        reset_storage()
        reset_config()

    def test_json_stdout_is_pure_json(self):
        import json

        result = CliRunner().invoke(
            app, ["drift", "no-such-suite", "--format", "json"]
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["suite_name"] == "no-such-suite"
        assert data["is_drifting"] is False

    def test_junit_stdout_parses_as_xml(self):
        import xml.etree.ElementTree as ET

        result = CliRunner().invoke(
            app, ["drift", "no-such-suite", "--format", "junit"]
        )
        assert result.exit_code == 0, result.stderr
        root = ET.fromstring(result.stdout)
        suite = root.find("testsuite")
        assert suite.get("failures") == "0"

    def test_json_output_flag_writes_file(self, tmp_path):
        import json

        out_path = tmp_path / "drift.json"
        result = CliRunner().invoke(
            app, ["drift", "no-such-suite", "--format", "json", "--output", str(out_path)]
        )
        assert result.exit_code == 0, result.stderr
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["suite_name"] == "no-such-suite"
        assert result.stdout.strip() == ""


class TestPricesCLI:
    """`promptry prices` — list, export, refresh (opt-in), and coverage."""

    @pytest.fixture(autouse=True)
    def _isolate_prices(self, tmp_path, monkeypatch):
        # never touch the real ~/.promptry/prices.json
        monkeypatch.setenv("PROMPTRY_PRICES_FILE", str(tmp_path / "prices.json"))
        from promptry import pricing
        rates = {k: dict(v) for k, v in pricing.RATES.items()}
        reroutes = dict(pricing.REROUTES)
        meta = dict(pricing.PRICES_META)
        yield
        pricing.RATES.clear(); pricing.RATES.update(rates)
        pricing.REROUTES.clear(); pricing.REROUTES.update(reroutes)
        pricing.PRICES_META.clear(); pricing.PRICES_META.update(meta)

    def test_list_shows_known_models(self):
        result = runner.invoke(app, ["prices"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.output
        assert "Model prices" in result.output

    def test_export_writes_feed(self, tmp_path):
        import json
        out = tmp_path / "feed.json"
        result = runner.invoke(app, ["prices", "--export", str(out)])
        assert result.exit_code == 0
        feed = json.loads(out.read_text(encoding="utf-8"))
        assert "gpt-4o" in feed["rates"]
        assert feed["reroutes"]["grok-3"] == ["2026-05-15", "grok-4.3"]

    def test_refresh_from_file_url_applies_and_persists(self, tmp_path):
        import json
        from promptry import pricing
        feed = {"version": "2026-09-09",
                "rates": {"cli-new-model": {"in": 1.0, "out": 2.0}}, "reroutes": {}}
        fp = tmp_path / "feed.json"
        fp.write_text(json.dumps(feed), encoding="utf-8")
        result = runner.invoke(app, ["prices", "--refresh", "--url", fp.as_uri()])
        assert result.exit_code == 0
        assert "cli-new-model" in result.output
        assert pricing.is_known_model("cli-new-model")
        assert (tmp_path / "prices.json").is_file()

    def test_refresh_bad_url_exits_nonzero(self):
        result = runner.invoke(app, ["prices", "--refresh", "--url", "file:///no/such/feed.json"])
        assert result.exit_code == 1
        assert "Refresh failed" in result.output

    def test_check_with_empty_ledger(self):
        result = runner.invoke(app, ["prices", "--check"])
        assert result.exit_code == 0
        assert "No models recorded" in result.output or "coverage" in result.output.lower()


class TestDashboard:

    def test_dashboard_local_flag_deprecated(self, monkeypatch):
        """dashboard --local should emit a deprecation warning."""
        # Mock uvicorn to avoid import error
        def mock_run(*args, **kwargs):
            pass

        import sys
        import types
        uvicorn = types.ModuleType('uvicorn')
        uvicorn.run = mock_run
        sys.modules['uvicorn'] = uvicorn

        result = runner.invoke(app, ["dashboard", "--local", "--no-open"])
        # Should see deprecation warning in output or exit with warning
        # The warning should appear before the error (if any)
        assert "Warning" in result.output or "deprecated" in result.output.lower()
