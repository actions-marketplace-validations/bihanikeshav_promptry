"""Tests for declarative YAML eval suites (promptry.yaml_suites)."""
import pytest

from promptry.evaluator import clear_suites, get_suite, list_suites
from promptry.yaml_suites import load_yaml_suites, YamlSuiteError, valid_assertion_keys


@pytest.fixture(autouse=True)
def _clean():
    clear_suites()
    yield
    clear_suites()


def _write(tmp_path, text, name="evals.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Schema parse + registration
# ---------------------------------------------------------------------------

class TestParseAndRegister:

    def test_registers_named_suites(self, tmp_path):
        path = _write(tmp_path, """
suites:
  - name: rag-quality
    pipeline: mypipe:run
    cases:
      - input: "hi"
        expect:
          - contains: "hello"
""")
        # provide the pipeline module on sys.path
        (tmp_path / "mypipe.py").write_text("def run(x):\n    return 'hello there'\n", encoding="utf-8")
        import sys
        sys.path.insert(0, str(tmp_path))
        try:
            names = load_yaml_suites(path)
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("mypipe", None)

        assert names == ["rag-quality"]
        assert get_suite("rag-quality") is not None
        assert [s.name for s in list_suites()] == ["rag-quality"]

    def test_description_is_preserved(self, tmp_path):
        path = _write(tmp_path, """
suites:
  - name: s1
    description: my blurb
    model: gpt-4o-mini
    prompt: "{input}"
    cases:
      - input: "x"
        expect: []
""")
        load_yaml_suites(path)
        assert get_suite("s1").description == "my blurb"

    def test_registered_suites_share_the_decorator_registry(self, tmp_path):
        """A YAML suite and a @suite-decorated one land in the same registry."""
        from promptry.evaluator import suite

        @suite("code-defined")
        def _code():
            pass

        path = _write(tmp_path, """
suites:
  - name: yaml-defined
    model: m
    prompt: "{input}"
    cases:
      - input: "x"
        expect: []
""")
        load_yaml_suites(path)
        names = {s.name for s in list_suites()}
        assert {"code-defined", "yaml-defined"} <= names


# ---------------------------------------------------------------------------
# End-to-end run with a stub pipeline
# ---------------------------------------------------------------------------

class TestRunWithStubPipeline:

    def test_passing_run(self, tmp_path, monkeypatch):
        (tmp_path / "stubmod.py").write_text(
            "def pipe(x):\n    return 'the refund policy is 30 days'\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        path = _write(tmp_path, """
suites:
  - name: refund
    pipeline: stubmod:pipe
    cases:
      - input: "refund policy?"
        expect:
          - contains: "30 days"
          - not_contains: "lawsuit"
""")
        load_yaml_suites(path)

        from promptry.storage import Storage
        from promptry.runner import run_suite
        storage = Storage(db_path=tmp_path / "t.db")
        try:
            result = run_suite("refund", storage=storage)
        finally:
            storage.close()

        assert result.overall_pass is True
        # two assertions ran (contains + not_contains)
        assert len(result.tests[0].assertions) == 2

    def test_failing_run(self, tmp_path, monkeypatch):
        (tmp_path / "stubmod2.py").write_text(
            "def pipe(x):\n    return 'no idea'\n", encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        path = _write(tmp_path, """
suites:
  - name: refund2
    pipeline: stubmod2:pipe
    cases:
      - input: "refund policy?"
        expect:
          - contains: "30 days"
""")
        load_yaml_suites(path)

        from promptry.storage import Storage
        from promptry.runner import run_suite
        storage = Storage(db_path=tmp_path / "t2.db")
        try:
            result = run_suite("refund2", storage=storage)
        finally:
            storage.close()

        assert result.overall_pass is False

    def test_multiple_cases_all_run(self, tmp_path, monkeypatch):
        (tmp_path / "stubmod3.py").write_text(
            "def pipe(x):\n    return x\n", encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        path = _write(tmp_path, """
suites:
  - name: echo
    pipeline: stubmod3:pipe
    cases:
      - input: "alpha"
        expect:
          - contains: "alpha"
      - input: "beta"
        expect:
          - contains: "beta"
""")
        load_yaml_suites(path)

        from promptry.storage import Storage
        from promptry.runner import run_suite
        storage = Storage(db_path=tmp_path / "t3.db")
        try:
            result = run_suite("echo", storage=storage)
        finally:
            storage.close()

        assert result.overall_pass is True
        assert len(result.tests[0].assertions) == 2


# ---------------------------------------------------------------------------
# Model-mode via mocked promptry.llm.complete
# ---------------------------------------------------------------------------

class TestModelMode:

    def test_model_mode_calls_complete(self, tmp_path, monkeypatch):
        calls = []

        def fake_complete(model, messages, **kwargs):
            calls.append((model, messages))
            return "answer: 42 tokens"

        monkeypatch.setattr("promptry.llm.complete", fake_complete)

        path = _write(tmp_path, """
suites:
  - name: model-suite
    model: gpt-4o-mini
    prompt: "Answer: {input}"
    cases:
      - input: "what is 6x7?"
        expect:
          - contains: "42"
""")
        load_yaml_suites(path)

        from promptry.storage import Storage
        from promptry.runner import run_suite
        storage = Storage(db_path=tmp_path / "m.db")
        try:
            result = run_suite("model-suite", storage=storage)
        finally:
            storage.close()

        assert result.overall_pass is True
        assert calls, "llm.complete should have been called"
        model, messages = calls[0]
        assert model == "gpt-4o-mini"
        # template substitution happened
        assert messages[0]["content"] == "Answer: what is 6x7?"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:

    def test_unknown_assertion_key(self, tmp_path):
        path = _write(tmp_path, """
suites:
  - name: bad
    model: m
    prompt: "{input}"
    cases:
      - input: "x"
        expect:
          - definitely_not_a_real_assertion: true
""")
        with pytest.raises(YamlSuiteError) as exc:
            load_yaml_suites(path)
        msg = str(exc.value)
        assert "definitely_not_a_real_assertion" in msg
        # the error names the valid keys
        for key in ("contains", "semantic", "json_valid"):
            assert key in msg

    def test_bad_yaml_reports_line(self, tmp_path):
        # invalid: a mapping value then a bad indent / unclosed bracket
        path = _write(tmp_path, """
suites:
  - name: broken
    cases: [unclosed
""")
        with pytest.raises(YamlSuiteError) as exc:
            load_yaml_suites(path)
        msg = str(exc.value)
        assert "line" in msg.lower()

    def test_missing_file(self, tmp_path):
        with pytest.raises(YamlSuiteError, match="not found"):
            load_yaml_suites(tmp_path / "nope.yaml")

    def test_no_suites_key(self, tmp_path):
        path = _write(tmp_path, "something_else: 1\n")
        with pytest.raises(YamlSuiteError, match="suites"):
            load_yaml_suites(path)

    def test_suite_without_pipeline_or_model(self, tmp_path):
        path = _write(tmp_path, """
suites:
  - name: nada
    cases:
      - input: "x"
        expect: []
""")
        with pytest.raises(YamlSuiteError, match="pipeline"):
            load_yaml_suites(path)

    def test_suite_missing_name(self, tmp_path):
        path = _write(tmp_path, """
suites:
  - model: m
    prompt: "{input}"
    cases:
      - input: "x"
        expect: []
""")
        with pytest.raises(YamlSuiteError, match="name"):
            load_yaml_suites(path)

    def test_empty_file(self, tmp_path):
        path = _write(tmp_path, "\n")
        with pytest.raises(YamlSuiteError, match="empty"):
            load_yaml_suites(path)

    def test_bad_pipeline_reference(self, tmp_path):
        path = _write(tmp_path, """
suites:
  - name: s
    pipeline: nonexistent_module_xyz:fn
    cases:
      - input: "x"
        expect:
          - contains: "y"
""")
        with pytest.raises(YamlSuiteError, match="import"):
            load_yaml_suites(path)


# ---------------------------------------------------------------------------
# schema assertion compiled from JSON-schema
# ---------------------------------------------------------------------------

class TestSchemaAssertion:

    def test_schema_from_json_schema(self, tmp_path, monkeypatch):
        (tmp_path / "jsonmod.py").write_text(
            'def pipe(x):\n    return \'{"amount": 5, "currency": "USD"}\'\n',
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        path = _write(tmp_path, """
suites:
  - name: schema-suite
    pipeline: jsonmod:pipe
    cases:
      - input: "x"
        expect:
          - json_valid: true
          - schema:
              type: object
              properties:
                amount: {type: number}
                currency: {type: string}
              required: [amount]
""")
        load_yaml_suites(path)

        from promptry.storage import Storage
        from promptry.runner import run_suite
        storage = Storage(db_path=tmp_path / "s.db")
        try:
            result = run_suite("schema-suite", storage=storage)
        finally:
            storage.close()

        assert result.overall_pass is True


class TestCliDiscovery:
    """CLI run/suites discover suites from an explicit .yaml or an
    auto-discovered evals.yaml/promptry.yaml."""

    def _runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    def test_suites_explicit_yaml_module(self, tmp_path, monkeypatch):
        from promptry.cli import app
        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t.db"))
        monkeypatch.chdir(tmp_path)
        (tmp_path / "evals.yaml").write_text("""
suites:
  - name: yaml-cli
    model: m
    prompt: "{input}"
    cases:
      - input: "x"
        expect: []
""", encoding="utf-8")
        result = self._runner().invoke(app, ["suites", "--module", "evals.yaml"])
        assert result.exit_code == 0
        assert "yaml-cli" in result.output

    def test_run_autodiscovers_evals_yaml(self, tmp_path, monkeypatch):
        from promptry.cli import app
        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t.db"))
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stubcli.py").write_text(
            "def pipe(x):\n    return 'contains the token'\n", encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        # No evals.py present -> auto-discovery kicks in for the default module.
        (tmp_path / "evals.yaml").write_text("""
suites:
  - name: auto
    pipeline: stubcli:pipe
    cases:
      - input: "x"
        expect:
          - contains: "token"
""", encoding="utf-8")
        result = self._runner().invoke(app, ["run", "auto"])
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output

    def test_unknown_assertion_surfaces_as_cli_error(self, tmp_path, monkeypatch):
        from promptry.cli import app
        monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "t.db"))
        monkeypatch.chdir(tmp_path)
        (tmp_path / "evals.yaml").write_text("""
suites:
  - name: bad
    model: m
    prompt: "{input}"
    cases:
      - input: "x"
        expect:
          - not_a_real_key: true
""", encoding="utf-8")
        result = self._runner().invoke(app, ["suites", "--module", "evals.yaml"])
        assert result.exit_code == 1
        assert "not_a_real_key" in result.output


def test_example_evals_yaml_parses(tmp_path):
    """The shipped examples/evals.yaml must be structurally valid."""
    import os
    from promptry.yaml_suites import load_yaml_suites
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    names = load_yaml_suites(os.path.join(here, "examples", "evals.yaml"))
    assert "rag-qa" in names and "extraction" in names


def test_valid_assertion_keys_covers_documented_set():
    keys = set(valid_assertion_keys())
    expected = {
        "contains", "not_contains", "regex", "exact", "semantic",
        "levenshtein", "rouge_l", "embedding_distance", "json_valid",
        "schema", "llm", "grounded",
    }
    assert keys == expected
