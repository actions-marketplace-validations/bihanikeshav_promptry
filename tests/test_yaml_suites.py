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


class TestLoadTimeValidation:
    """Missing required sub-keys must fail at LOAD time with a YamlSuiteError,
    not surface at run time as a KeyError recorded as a failed assertion."""

    def _load(self, tmp_path, expect_block):
        path = _write(tmp_path, f"""
suites:
  - name: v
    model: m
    prompt: "{{input}}"
    cases:
      - input: "x"
        expect:
{expect_block}
""")
        return load_yaml_suites(path)

    @pytest.mark.parametrize("expect_block,needle", [
        # mapping form missing its required sub-key
        ("          - semantic: {threshold: 0.5}", "expected"),
        ("          - exact: {case_sensitive: true}", "expected"),
        ("          - regex: {fullmatch: false}", "pattern"),
        ("          - llm: {threshold: 0.5}", "criteria"),
        ("          - grounded: {threshold: 0.5}", "source"),
        ("          - rouge_l: {expected: hi}", "min_score"),
        ("          - rouge_l: {min_score: 0.5}", "expected"),
        ("          - embedding_distance: {expected: hi}", "max_distance"),
        ("          - embedding_distance: {max_distance: 0.5}", "expected"),
        ("          - levenshtein: {min_ratio: 0.5}", "expected"),
    ])
    def test_missing_required_subkey_fails_at_load(self, tmp_path, expect_block, needle):
        with pytest.raises(YamlSuiteError) as exc:
            self._load(tmp_path, expect_block)
        assert needle in str(exc.value)

    def test_levenshtein_requires_exactly_one_threshold_neither(self, tmp_path):
        with pytest.raises(YamlSuiteError, match="exactly one"):
            self._load(tmp_path, "          - levenshtein: {expected: hi}")

    def test_levenshtein_requires_exactly_one_threshold_both(self, tmp_path):
        with pytest.raises(YamlSuiteError, match="exactly one"):
            self._load(
                tmp_path,
                "          - levenshtein: {expected: hi, max_distance: 2, min_ratio: 0.5}",
            )

    def test_empty_mapping_where_string_expected(self, tmp_path):
        with pytest.raises(YamlSuiteError, match="expected"):
            self._load(tmp_path, "          - semantic: {}")

    @pytest.mark.parametrize("key", ["contains", "not_contains"])
    def test_contains_rejects_non_string_values(self, tmp_path, key):
        with pytest.raises(YamlSuiteError, match="string"):
            self._load(tmp_path, f"          - {key}: {{bad: mapping}}")

    @pytest.mark.parametrize("key", ["contains", "not_contains"])
    def test_contains_rejects_non_string_list_items(self, tmp_path, key):
        with pytest.raises(YamlSuiteError, match="string"):
            self._load(tmp_path, f"          - {key}: [ok, 42]")

    def test_valid_forms_still_load(self, tmp_path):
        # sanity: every documented valid form still compiles at load time
        names = self._load(tmp_path, """
          - contains: "a"
          - contains: [a, b]
          - not_contains: "z"
          - regex: "(a|b)"
          - regex: {pattern: "a", fullmatch: false}
          - exact: "a"
          - exact: {expected: "a", case_sensitive: false}
          - semantic: {expected: "a", threshold: 0.5}
          - levenshtein: {expected: "a", min_ratio: 0.5}
          - levenshtein: {expected: "a", max_distance: 2}
          - rouge_l: {expected: "a", min_score: 0.5}
          - embedding_distance: {expected: "a", max_distance: 0.5}
          - json_valid: true
          - llm: "criteria here"
          - llm: {criteria: "c", threshold: 0.5}
          - grounded: {source: "s"}
""")
        assert names == ["v"]


class TestSchemaUnsupportedConstructs:
    """The JSON-schema compiler must reject constructs it can't enforce,
    instead of silently degrading them to Any (false PASSes)."""

    def _load_schema(self, tmp_path, schema_yaml):
        path = _write(tmp_path, f"""
suites:
  - name: s
    model: m
    prompt: "{{input}}"
    cases:
      - input: "x"
        expect:
          - schema:
{schema_yaml}
""")
        return load_yaml_suites(path)

    def test_enum_rejected(self, tmp_path):
        with pytest.raises(YamlSuiteError) as exc:
            self._load_schema(tmp_path, """
              type: object
              properties:
                status: {type: string, enum: [open, closed]}
""")
        assert "enum" in str(exc.value)

    def test_ref_rejected(self, tmp_path):
        with pytest.raises(YamlSuiteError) as exc:
            self._load_schema(tmp_path, """
              type: object
              properties:
                nested: {"$ref": "#/definitions/thing"}
""")
        assert "$ref" in str(exc.value)

    def test_anyof_rejected(self, tmp_path):
        with pytest.raises(YamlSuiteError) as exc:
            self._load_schema(tmp_path, """
              type: object
              properties:
                value:
                  anyOf:
                    - {type: string}
                    - {type: number}
""")
        assert "anyOf" in str(exc.value)

    def test_array_items_rejected(self, tmp_path):
        with pytest.raises(YamlSuiteError) as exc:
            self._load_schema(tmp_path, """
              type: object
              properties:
                tags:
                  type: array
                  items: {type: string}
""")
        assert "items" in str(exc.value)

    def test_nested_object_properties_rejected(self, tmp_path):
        with pytest.raises(YamlSuiteError) as exc:
            self._load_schema(tmp_path, """
              type: object
              properties:
                address:
                  type: object
                  properties:
                    city: {type: string}
""")
        msg = str(exc.value).lower()
        assert "nested" in msg or "properties" in msg

    def test_error_names_supported_subset(self, tmp_path):
        with pytest.raises(YamlSuiteError) as exc:
            self._load_schema(tmp_path, """
              type: object
              properties:
                status: {type: string, enum: [a]}
""")
        # points the user at what IS supported
        assert "type" in str(exc.value) and "properties" in str(exc.value)

    def test_flat_schema_still_works(self, tmp_path):
        names = self._load_schema(tmp_path, """
              type: object
              properties:
                amount: {type: number}
                name: {type: string}
              required: [amount]
""")
        assert names == ["s"]


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
