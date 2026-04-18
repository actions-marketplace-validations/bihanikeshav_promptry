"""LLM-powered generation of eval test cases from a human-written spec.

The user writes a small YAML/TOML spec describing what they want and this
module asks the configured LLM judge (see promptry.assertions.set_judge)
to emit N diverse test cases, then renders them into a ready-to-run
@suite-decorated Python file.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------


@dataclass
class DatasetSpec:
    """Parsed representation of a dataset generation spec file."""

    suite_name: str
    description: str = ""
    count: int = 10
    seed_cases: list[dict[str, Any]] = field(default_factory=list)
    difficulty: str = "mixed"  # easy | medium | hard | mixed
    assertion_style: str = "contains"  # contains | semantic | schema


_ALLOWED_DIFFICULTY = {"easy", "medium", "hard", "mixed"}
_ALLOWED_STYLES = {"contains", "semantic", "schema"}


def load_spec(path: Path) -> DatasetSpec:
    """Load a spec file (.yaml/.yml or .toml) into a DatasetSpec."""
    if not path.is_file():
        raise FileNotFoundError(f"Spec file not found: {path}")

    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8")

    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "YAML spec requires 'pyyaml'. Install with: pip install pyyaml\n"
                "Alternatively, convert the spec to TOML (.toml)."
            ) from e
        data = yaml.safe_load(raw) or {}
    elif suffix == ".toml":
        if sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:
            try:
                import tomllib as _tomllib  # type: ignore[no-redef]
            except ImportError:
                import tomli as _tomllib  # type: ignore[no-redef]
        data = _tomllib.loads(raw)
    else:
        raise ValueError(
            f"Unsupported spec extension '{suffix}'. Use .yaml, .yml, or .toml."
        )

    if not isinstance(data, dict):
        raise ValueError("Spec file must contain a mapping at the top level.")

    return _spec_from_dict(data)


def _spec_from_dict(data: dict[str, Any]) -> DatasetSpec:
    suite_name = str(data.get("suite_name", "") or "").strip()
    if not suite_name:
        raise ValueError("Spec is missing required field: 'suite_name'.")

    count = int(data.get("count", 10) or 10)
    if count <= 0:
        raise ValueError("'count' must be a positive integer.")

    difficulty = str(data.get("difficulty", "mixed") or "mixed").lower()
    if difficulty not in _ALLOWED_DIFFICULTY:
        raise ValueError(
            f"Invalid 'difficulty' {difficulty!r}; "
            f"must be one of {sorted(_ALLOWED_DIFFICULTY)}."
        )

    assertion_style = str(data.get("assertion_style", "contains") or "contains").lower()
    if assertion_style not in _ALLOWED_STYLES:
        raise ValueError(
            f"Invalid 'assertion_style' {assertion_style!r}; "
            f"must be one of {sorted(_ALLOWED_STYLES)}."
        )

    seed_cases = data.get("seed_cases") or []
    if not isinstance(seed_cases, list):
        raise ValueError("'seed_cases' must be a list.")
    # normalize seeds
    normed_seeds = []
    for s in seed_cases:
        if not isinstance(s, dict):
            raise ValueError("each seed_case must be a mapping.")
        normed_seeds.append(dict(s))

    return DatasetSpec(
        suite_name=suite_name,
        description=str(data.get("description", "") or ""),
        count=count,
        seed_cases=normed_seeds,
        difficulty=difficulty,
        assertion_style=assertion_style,
    )


# ---------------------------------------------------------------------------
# Judge prompting / parsing
# ---------------------------------------------------------------------------


_GEN_PROMPT = """You are helping a developer build a test suite for an LLM pipeline.

Task: generate {count} diverse, realistic test cases for the following eval suite.

Suite name: {suite_name}
Description: {description}
Difficulty: {difficulty}

{seed_block}

Respond with ONLY a JSON array of exactly {count} objects. Each object must
have these fields:
  - "question": the input to send to the LLM pipeline (string)
  - "expected_answer_fragment": a short substring or phrase the answer
    should contain (string)
  - "difficulty": one of "easy", "medium", "hard"

Do not include markdown code fences or any prose outside the JSON array.
Example output:
[{{"question": "...", "expected_answer_fragment": "...", "difficulty": "easy"}}]
"""


_STRICTER_SUFFIX = (
    "\n\nYour previous response was not valid JSON. Respond with ONLY a raw "
    "JSON array, no markdown, no commentary, no code fences."
)


def _build_prompt(spec: DatasetSpec, count: int) -> str:
    if spec.seed_cases:
        seed_lines = ["Seed examples (match the style, topic and tone):"]
        for s in spec.seed_cases:
            q = s.get("question", "")
            frag = s.get("expected_contains") or s.get("expected_answer_fragment") or ""
            seed_lines.append(f'  - Q: {q!r}  expected_contains: {frag!r}')
        seed_block = "\n".join(seed_lines)
    else:
        seed_block = "No seed examples provided; invent plausible ones."

    return _GEN_PROMPT.format(
        count=count,
        suite_name=spec.suite_name,
        description=spec.description or "(no description)",
        difficulty=spec.difficulty,
        seed_block=seed_block,
    )


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    """Best-effort JSON array extractor.

    Handles: markdown code fences, leading/trailing prose, stray whitespace.
    Raises ValueError on unparseable input.
    """
    text = raw.strip()
    # strip code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # try direct parse first
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # fall back to locating the outermost [...] span
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON array found in judge output")
        snippet = text[start : end + 1]
        data = json.loads(snippet)  # let the JSONDecodeError propagate

    if not isinstance(data, list):
        raise ValueError("judge output was not a JSON array")

    cases: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"item {i} is not an object")
        cases.append(item)
    return cases


def generate_cases(
    spec: DatasetSpec,
    judge: Callable[[str], str],
    *,
    count: int | None = None,
) -> list[dict[str, Any]]:
    """Ask the judge LLM for N cases; retry once on parse failure."""
    n = count if count is not None else spec.count
    prompt = _build_prompt(spec, n)

    raw_first = judge(prompt)
    try:
        return _extract_json_array(raw_first)
    except (ValueError, json.JSONDecodeError):
        pass

    # one strict retry
    raw_second = judge(prompt + _STRICTER_SUFFIX)
    try:
        return _extract_json_array(raw_second)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(
            "Judge returned unparseable output twice.\n"
            f"First response:\n{raw_first[:500]}\n\n"
            f"Second response:\n{raw_second[:500]}"
        ) from e


# ---------------------------------------------------------------------------
# Python file rendering
# ---------------------------------------------------------------------------


_SUITE_FN_NAME_RE = re.compile(r"[^0-9a-zA-Z_]+")


def _suite_func_name(suite_name: str) -> str:
    slug = _SUITE_FN_NAME_RE.sub("_", suite_name).strip("_").lower()
    if not slug:
        slug = "generated"
    if slug[0].isdigit():
        slug = "_" + slug
    return f"test_{slug}"


def render_python_file(
    spec: DatasetSpec,
    cases: Iterable[dict[str, Any]],
) -> str:
    """Render cases into a ready-to-run Python file using @suite."""
    cases = list(cases)
    style = spec.assertion_style

    if style == "contains":
        import_line = "from promptry import suite, assert_contains"
    elif style == "semantic":
        import_line = "from promptry import suite, assert_semantic"
    elif style == "schema":
        import_line = "from promptry import suite, assert_json_valid"
    else:  # pragma: no cover - guarded by _spec_from_dict
        import_line = "from promptry import suite, assert_contains"

    lines: list[str] = []
    lines.append('"""Generated by promptry dataset generate. Edit as needed."""')
    lines.append(import_line)
    lines.append("")
    lines.append("")
    lines.append("def my_pipeline(q: str) -> str:")
    lines.append('    raise NotImplementedError("Wire up your LLM pipeline here.")')
    lines.append("")
    lines.append("")
    # json.dumps gives us double-quoted, properly-escaped strings which is
    # what we want for the suite decorator (matches the convention in the
    # generated file examples).
    lines.append(f"@suite({json.dumps(spec.suite_name)})")
    lines.append(f"def {_suite_func_name(spec.suite_name)}():")
    if spec.description:
        lines.append(f'    """{spec.description}"""')

    if not cases:
        lines.append("    pass")
    else:
        for c in cases:
            q = str(c.get("question", "")).strip()
            frag = str(
                c.get("expected_answer_fragment")
                or c.get("expected_contains")
                or ""
            ).strip()
            if not q:
                continue
            if style == "contains":
                lines.append(f"    assert_contains(my_pipeline({q!r}), {frag!r})")
            elif style == "semantic":
                lines.append(f"    assert_semantic(my_pipeline({q!r}), {frag!r})")
            elif style == "schema":
                # Without a schema in the spec we fall back to JSON-validity.
                lines.append(f"    assert_json_valid(my_pipeline({q!r}))")

    lines.append("")  # trailing newline
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level orchestration (used by the CLI)
# ---------------------------------------------------------------------------


@dataclass
class GenerateResult:
    spec: DatasetSpec
    cases: list[dict[str, Any]]
    code: str


def generate_from_spec(
    spec_path: Path,
    judge: Callable[[str], str],
    *,
    count: int | None = None,
) -> GenerateResult:
    """Load spec, call judge, render code. Raises on any failure."""
    spec = load_spec(spec_path)
    cases = generate_cases(spec, judge, count=count)
    code = render_python_file(spec, cases)
    return GenerateResult(spec=spec, cases=cases, code=code)
