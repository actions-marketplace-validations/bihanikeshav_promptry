"""Declarative YAML eval suites.

Lets teams author eval suites without writing Python. A YAML file is parsed,
each suite is compiled into an ordinary suite function, and that function is
registered into the SAME registry the ``@suite`` decorator uses
(:mod:`promptry.evaluator`). Once registered, ``promptry run`` / ``watch`` /
``drift`` / ``history`` / the dashboard treat a YAML-defined suite exactly like
a code-defined one -- there is no second execution path.

Schema
------
::

    suites:
      - name: rag-quality
        description: optional human blurb
        pipeline: mymodule:my_pipeline    # optional; "module:function"
        # ...OR a direct model call (used when `pipeline` is absent):
        model: gpt-4o-mini                # routed through promptry.llm.complete
        prompt: "Answer: {input}"         # template; {input} is substituted
        cases:
          - input: "What is our refund policy?"
            expect:
              - contains: "30 days"                    # str or [str, ...]
              - not_contains: "lawsuit"
              - regex: "(refund|return)"               # or {pattern, fullmatch}
              - exact: "yes"                           # or {expected, case_sensitive}
              - semantic: {expected: "Refunds within 30 days", threshold: 0.75}
              - levenshtein: {expected: "30 days", min_ratio: 0.8}
              - rouge_l: {expected: "refund within 30 days", min_score: 0.5}
              - embedding_distance: {expected: "30 day refunds", max_distance: 0.3}
              - json_valid: true
              - schema: {type: object, properties: {amount: {type: number}}, required: [amount]}
              - llm: "Is the answer grounded and polite?"   # or {criteria, threshold}
              - grounded: {source: "Refunds are allowed within 30 days.", threshold: 0.8}

Each assertion key maps 1:1 onto the corresponding ``assert_*`` function, so
the behaviour (scoring, failure messages, result details) is identical to the
code path.

Public entry point: :func:`load_yaml_suites`.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from promptry.evaluator import suite, check_all
from promptry.assertions import (
    assert_contains,
    assert_not_contains,
    assert_matches,
    assert_exact,
    assert_semantic,
    assert_levenshtein,
    assert_rouge_l,
    assert_embedding_distance,
    assert_json_valid,
    assert_schema,
    assert_llm,
    assert_grounded,
)


class YamlSuiteError(ValueError):
    """Raised when a YAML suite file is malformed or references unknown keys.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers in the
    CLI keep working.
    """


# ---------------------------------------------------------------------------
# Assertion dispatch: YAML key -> adapter that calls the matching assert_*.
# Each adapter takes (output, value) and returns the assertion's score.
# ---------------------------------------------------------------------------

def _as_keywords(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _check_contains(output, value):
    return assert_contains(output, _as_keywords(value))


def _check_not_contains(output, value):
    return assert_not_contains(output, _as_keywords(value))


def _check_regex(output, value):
    if isinstance(value, dict):
        return assert_matches(output, value["pattern"], fullmatch=value.get("fullmatch", True))
    return assert_matches(output, value)


def _check_exact(output, value):
    if isinstance(value, dict):
        return assert_exact(output, value["expected"], value.get("case_sensitive", True))
    return assert_exact(output, value)


def _check_semantic(output, value):
    if isinstance(value, dict):
        return assert_semantic(output, value["expected"], value.get("threshold"))
    return assert_semantic(output, value)


def _check_levenshtein(output, value):
    if not isinstance(value, dict):
        raise YamlSuiteError("'levenshtein' expects a mapping with 'expected' and one of max_distance/min_ratio")
    return assert_levenshtein(
        output,
        value["expected"],
        max_distance=value.get("max_distance"),
        min_ratio=value.get("min_ratio"),
    )


def _check_rouge_l(output, value):
    if not isinstance(value, dict):
        raise YamlSuiteError("'rouge_l' expects a mapping with 'expected' and 'min_score'")
    return assert_rouge_l(output, value["expected"], value["min_score"])


def _check_embedding_distance(output, value):
    if not isinstance(value, dict):
        raise YamlSuiteError("'embedding_distance' expects a mapping with 'expected' and 'max_distance'")
    return assert_embedding_distance(output, value["expected"], value["max_distance"])


def _check_json_valid(output, value):
    return assert_json_valid(output)


def _check_schema(output, value):
    # value is a pre-built pydantic model (compiled at load time).
    return assert_schema(output, value)


def _check_llm(output, value):
    if isinstance(value, dict):
        return assert_llm(output, value["criteria"], value.get("threshold", 0.7))
    return assert_llm(output, value)


def _check_grounded(output, value):
    if isinstance(value, dict):
        return assert_grounded(output, value["source"], value.get("threshold", 0.8))
    return assert_grounded(output, value)


_DISPATCH: dict[str, Callable[[Any, Any], float]] = {
    "contains": _check_contains,
    "not_contains": _check_not_contains,
    "regex": _check_regex,
    "exact": _check_exact,
    "semantic": _check_semantic,
    "levenshtein": _check_levenshtein,
    "rouge_l": _check_rouge_l,
    "embedding_distance": _check_embedding_distance,
    "json_valid": _check_json_valid,
    "schema": _check_schema,
    "llm": _check_llm,
    "grounded": _check_grounded,
}


def valid_assertion_keys() -> list[str]:
    """The assertion keys accepted in a case's ``expect`` list."""
    return sorted(_DISPATCH)


# ---------------------------------------------------------------------------
# Load-time validation of assertion values.
#
# Required sub-keys are checked while compiling the YAML, so a typo'd file
# fails fast with a YamlSuiteError instead of surfacing at run time as a
# KeyError recorded (and persisted) as a failed assertion.
# ---------------------------------------------------------------------------

def _require_subkeys(key: str, value: dict, required: tuple, where: str) -> None:
    missing = [k for k in required if k not in value]
    if missing:
        raise YamlSuiteError(
            f"{where}: '{key}' is missing required key(s): {', '.join(missing)} "
            f"(got keys: {', '.join(sorted(value)) or '(none)'})"
        )


def _validate_str_or_mapping(key: str, value: Any, required: tuple, where: str) -> None:
    """Value may be a plain string, or a mapping carrying ``required`` keys."""
    if isinstance(value, str):
        return
    if isinstance(value, dict):
        _require_subkeys(key, value, required, where)
        return
    raise YamlSuiteError(
        f"{where}: '{key}' must be a string or a mapping with "
        f"{'/'.join(required)}, got {type(value).__name__}"
    )


def _validate_mapping_only(key: str, value: Any, required: tuple, where: str) -> None:
    """Value must be a mapping carrying all ``required`` keys."""
    if not isinstance(value, dict):
        raise YamlSuiteError(
            f"{where}: '{key}' must be a mapping with {', '.join(required)}, "
            f"got {type(value).__name__}"
        )
    _require_subkeys(key, value, required, where)


def _validate_keywords(key: str, value: Any, where: str) -> None:
    """contains/not_contains: a string or a list of strings."""
    if isinstance(value, str):
        return
    if isinstance(value, (list, tuple)):
        bad = [v for v in value if not isinstance(v, str)]
        if bad:
            raise YamlSuiteError(
                f"{where}: '{key}' list items must all be strings, "
                f"got {bad[0]!r} ({type(bad[0]).__name__})"
            )
        return
    raise YamlSuiteError(
        f"{where}: '{key}' must be a string or a list of strings, "
        f"got {type(value).__name__}"
    )


def _validate_levenshtein(value: Any, where: str) -> None:
    _validate_mapping_only("levenshtein", value, ("expected",), where)
    if ("max_distance" in value) == ("min_ratio" in value):
        raise YamlSuiteError(
            f"{where}: 'levenshtein' requires exactly one of "
            f"max_distance or min_ratio"
        )


def _validate_assertion_value(key: str, value: Any, where: str) -> None:
    """Load-time shape check for one assertion entry."""
    if key in ("contains", "not_contains"):
        _validate_keywords(key, value, where)
    elif key == "regex":
        _validate_str_or_mapping(key, value, ("pattern",), where)
    elif key in ("exact", "semantic"):
        _validate_str_or_mapping(key, value, ("expected",), where)
    elif key == "llm":
        _validate_str_or_mapping(key, value, ("criteria",), where)
    elif key == "grounded":
        _validate_str_or_mapping(key, value, ("source",), where)
    elif key == "levenshtein":
        _validate_levenshtein(value, where)
    elif key == "rouge_l":
        _validate_mapping_only(key, value, ("expected", "min_score"), where)
    elif key == "embedding_distance":
        _validate_mapping_only(key, value, ("expected", "max_distance"), where)
    # json_valid: any truthy marker is fine; schema: validated by the compiler.


# ---------------------------------------------------------------------------
# JSON-schema -> pydantic model (so `schema:` can be authored in YAML and still
# flow through the existing assert_schema, which validates against a model).
# ---------------------------------------------------------------------------

_JSON_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


# JSON-schema constructs the compiler cannot enforce. Silently degrading them
# to Any would make e.g. an `enum` author believe values are being checked
# when they are not (false PASS), so they are rejected loudly at load time.
_UNSUPPORTED_SCHEMA_KEYS = (
    "$ref", "anyOf", "oneOf", "allOf", "not", "enum", "items",
    "patternProperties", "additionalProperties", "if", "then", "else",
)

_SCHEMA_SUPPORTED_MSG = (
    "the supported subset is a flat object schema: "
    "'type', 'properties' (each with a scalar 'type': "
    "string/integer/number/boolean/array/object), and 'required'. "
    "For anything richer, define the suite in Python with a pydantic model."
)


def _reject_unsupported_schema(spec: dict, where: str) -> None:
    for bad in _UNSUPPORTED_SCHEMA_KEYS:
        if bad in spec:
            raise YamlSuiteError(
                f"'schema' {where} uses unsupported construct '{bad}' -- "
                f"{_SCHEMA_SUPPORTED_MSG}"
            )


def _model_from_json_schema(schema: Any):
    if not isinstance(schema, dict):
        raise YamlSuiteError("'schema' must be a JSON-schema mapping (e.g. {type: object, properties: {...}})")
    from pydantic import create_model

    _reject_unsupported_schema(schema, "(top level)")

    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields: dict[str, tuple] = {}
    for fname, fspec in props.items():
        fspec = fspec or {}
        if not isinstance(fspec, dict):
            raise YamlSuiteError(
                f"'schema' property '{fname}' must be a mapping like "
                f"{{type: string}}, got {type(fspec).__name__}"
            )
        _reject_unsupported_schema(fspec, f"property '{fname}'")
        if "properties" in fspec:
            raise YamlSuiteError(
                f"'schema' property '{fname}' declares nested 'properties', "
                f"which the YAML schema compiler cannot enforce -- "
                f"{_SCHEMA_SUPPORTED_MSG}"
            )
        ftype_name = fspec.get("type")
        if ftype_name is not None and ftype_name not in _JSON_TYPES:
            raise YamlSuiteError(
                f"'schema' property '{fname}' has unsupported type "
                f"{ftype_name!r} -- {_SCHEMA_SUPPORTED_MSG}"
            )
        ftype = _JSON_TYPES.get(ftype_name, Any)
        if fname in required:
            fields[fname] = (ftype, ...)
        else:
            fields[fname] = (Optional[ftype], None)
    return create_model("YamlSchema", **fields)


# ---------------------------------------------------------------------------
# Output production (pipeline call, or a direct model call via promptry.llm).
# ---------------------------------------------------------------------------

def _resolve_pipeline(spec: str) -> Callable:
    """Resolve a ``"module:function"`` string to the callable."""
    mod_name, sep, func_name = spec.partition(":")
    if not sep or not func_name:
        raise YamlSuiteError(
            f"pipeline must be in 'module:function' form, got {spec!r}"
        )
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001 - surface any import failure clearly
        raise YamlSuiteError(f"Could not import pipeline module '{mod_name}': {e}") from e
    fn = getattr(mod, func_name, None)
    if fn is None:
        raise YamlSuiteError(f"Module '{mod_name}' has no attribute '{func_name}'")
    if not callable(fn):
        raise YamlSuiteError(f"'{spec}' is not callable")
    return fn


def _render_template(template: str, input_val: Any) -> str:
    """Substitute ``{input}`` in a model-mode prompt template.

    A literal ``.replace`` (not ``str.format``) so JSON braces or other ``{}``
    in the template are left untouched.
    """
    if template is None:
        return "" if input_val is None else str(input_val)
    return template.replace("{input}", "" if input_val is None else str(input_val))


def _make_output_fn(pipeline_fn, model, prompt_template) -> Callable[[Any], str]:
    if pipeline_fn is not None:
        return lambda input_val: pipeline_fn(input_val)

    def _model_call(input_val):
        # Call via the module attribute so tests can monkeypatch
        # promptry.llm.complete and have it picked up here.
        import promptry.llm as _llm
        text = _render_template(prompt_template, input_val)
        return _llm.complete(model, [{"role": "user", "content": text}])

    return _model_call


# ---------------------------------------------------------------------------
# Compilation + registration.
# ---------------------------------------------------------------------------

def _compile_expect(expect: Any, where: str) -> list[tuple[Callable, Any]]:
    """Turn a case's ``expect`` list into [(handler, compiled_value), ...]."""
    if expect is None:
        return []
    if not isinstance(expect, list):
        raise YamlSuiteError(f"{where}: 'expect' must be a list of assertions")

    compiled: list[tuple[Callable, Any]] = []
    for entry in expect:
        if not isinstance(entry, dict):
            raise YamlSuiteError(
                f"{where}: each 'expect' item must be a mapping like "
                f"'- contains: ...', got {entry!r}"
            )
        for key, value in entry.items():
            handler = _DISPATCH.get(key)
            if handler is None:
                raise YamlSuiteError(
                    f"{where}: unknown assertion '{key}'. "
                    f"Valid keys: {', '.join(valid_assertion_keys())}"
                )
            if key == "schema":
                value = _model_from_json_schema(value)
            else:
                _validate_assertion_value(key, value, where)
            compiled.append((handler, value))
    return compiled


def _make_suite_fn(output_fn, compiled_cases):
    """Build the suite function that runs every case and aggregates checks."""
    def _fn():
        checks: list[Callable[[], float]] = []
        for input_val, expects in compiled_cases:
            output = output_fn(input_val)
            for handler, value in expects:
                checks.append(
                    (lambda h, o, v: (lambda: h(o, v)))(handler, output, value)
                )
        return check_all(*checks)

    return _fn


def _register_suite(spec: Any, index: int, source: str) -> str:
    where = f"{source}: suites[{index}]"
    if not isinstance(spec, dict):
        raise YamlSuiteError(f"{where}: each suite must be a mapping")

    name = spec.get("name")
    if not name or not isinstance(name, str):
        raise YamlSuiteError(f"{where}: suite is missing a string 'name'")

    pipeline_spec = spec.get("pipeline")
    model = spec.get("model")
    prompt_template = spec.get("prompt")

    if pipeline_spec:
        pipeline_fn = _resolve_pipeline(pipeline_spec)
        output_fn = _make_output_fn(pipeline_fn, None, None)
    elif model:
        output_fn = _make_output_fn(None, model, prompt_template)
    else:
        raise YamlSuiteError(
            f"{where} ('{name}'): needs either a 'pipeline: module:function' "
            f"or a 'model:' for a direct model call"
        )

    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        raise YamlSuiteError(f"{where} ('{name}'): needs a non-empty 'cases' list")

    compiled_cases: list[tuple[Any, list]] = []
    for ci, case in enumerate(cases):
        if not isinstance(case, dict):
            raise YamlSuiteError(f"{where} ('{name}'): cases[{ci}] must be a mapping")
        expects = _compile_expect(case.get("expect"), f"{where} ('{name}') cases[{ci}]")
        compiled_cases.append((case.get("input"), expects))

    fn = _make_suite_fn(output_fn, compiled_cases)
    fn.__name__ = f"yaml_suite_{name}".replace("-", "_")
    suite(name, description=spec.get("description", ""))(fn)
    return name


def _format_yaml_error(path: Path, err: yaml.YAMLError) -> str:
    """Build a message with line/column context from a YAML parse error."""
    mark = getattr(err, "problem_mark", None)
    problem = getattr(err, "problem", None) or str(err)
    if mark is not None:
        return (
            f"Invalid YAML in {path} at line {mark.line + 1}, column {mark.column + 1}: "
            f"{problem}"
        )
    return f"Invalid YAML in {path}: {problem}"


def load_yaml_suites(path) -> list[str]:
    """Parse a YAML suite file and register every suite it declares.

    Returns the list of registered suite names. Registration goes through the
    same :func:`promptry.evaluator.suite` decorator the code path uses, so the
    resulting suites are indistinguishable from code-defined ones.

    Raises :class:`YamlSuiteError` (a ``ValueError``) for a missing file,
    malformed YAML (with line/column context), an unknown assertion key, or any
    structural problem.
    """
    path = Path(path)
    if not path.is_file():
        raise YamlSuiteError(f"YAML suite file not found: {path}")

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise YamlSuiteError(_format_yaml_error(path, e)) from e

    if data is None:
        raise YamlSuiteError(f"{path}: file is empty (expected a top-level 'suites:' list)")
    if not isinstance(data, dict) or "suites" not in data:
        raise YamlSuiteError(f"{path}: expected a top-level 'suites:' key")

    suites = data["suites"]
    if not isinstance(suites, list) or not suites:
        raise YamlSuiteError(f"{path}: 'suites' must be a non-empty list")

    names: list[str] = []
    for index, spec in enumerate(suites):
        names.append(_register_suite(spec, index, str(path)))
    return names
