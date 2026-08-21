"""`promptry new ...` subcommands: scaffold new eval suites (the wizard)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from promptry.commands._base import console, new_app

# The simple-value subset of promptry.yaml_suites' assertion keys that the
# "--case" flag grammar covers. Richer assertions (semantic, schema,
# levenshtein, rouge_l, embedding_distance, grounded, json_valid) aren't
# expressible as a single string value, so they're documented as a
# hand-edit-the-YAML follow-up instead.
_CASE_ASSERTIONS = ("contains", "not_contains", "regex", "exact", "llm")

# assertion key -> assert_* function name (as importable from `promptry`).
_CASE_ASSERTION_FUNCS = {
    "contains": "assert_contains",
    "not_contains": "assert_not_contains",
    "regex": "assert_matches",
    "exact": "assert_exact",
    "llm": "assert_llm",
}


def _parse_case_flag(raw: str) -> tuple[str, str, str]:
    """Parse one ``--case "input::assertion::value"`` flag value."""
    parts = raw.split("::", 2)
    if len(parts) != 3:
        raise ValueError(
            f"Invalid --case syntax: {raw!r}. Expected 'input::assertion::value' "
            f"(assertion one of: {', '.join(_CASE_ASSERTIONS)})."
        )
    input_val, assertion, value = parts
    if not input_val:
        raise ValueError(f"Invalid --case syntax: empty input in {raw!r}.")
    if assertion not in _CASE_ASSERTIONS:
        raise ValueError(
            f"Invalid --case syntax: unknown assertion {assertion!r} in {raw!r}. "
            f"Valid assertions: {', '.join(_CASE_ASSERTIONS)}."
        )
    return input_val, assertion, value


def _group_cases(parsed: list) -> list:
    """Group (input, assertion, value) triples into one case per distinct
    input, preserving first-seen order, so repeated --case flags for the
    same input become multiple `expect` entries on a single case.
    """
    order: list = []
    by_input: dict = {}
    for input_val, assertion, value in parsed:
        if input_val not in by_input:
            by_input[input_val] = []
            order.append(input_val)
        by_input[input_val].append((assertion, value))
    return [(inp, by_input[inp]) for inp in order]


def _build_yaml_suite_dict(name, description, pipeline, model, prompt_template, grouped_cases) -> dict:
    suite_dict: dict = {"name": name}
    if description:
        suite_dict["description"] = description
    if pipeline:
        suite_dict["pipeline"] = pipeline
    else:
        suite_dict["model"] = model
        suite_dict["prompt"] = prompt_template
    suite_dict["cases"] = [
        {"input": inp, "expect": [{a: v} for a, v in expects]}
        for inp, expects in grouped_cases
    ]
    return suite_dict


def _write_yaml_suite(path: Path, suite_dict: dict) -> None:
    """Append ``suite_dict`` to the ``suites:`` list in ``path``, creating the
    file (or replacing a comments-only starter scaffold) if needed.

    Round-trips the whole document through yaml.safe_load/safe_dump, which is
    correct but re-serializes the file -- any existing comments/formatting in
    a *non-empty* evals.yaml are not preserved (an accepted wizard trade-off;
    see the task report).

    Never clobbers a file it can't safely merge into: invalid YAML, or a
    document that isn't the expected ``{suites: [...]}`` shape, raises
    ValueError and leaves the file untouched. A file that parses to ``None``
    (empty, or the comments-only ``init`` scaffold) is treated as fresh.

    The actual write lives in :func:`promptry.suite_builder.write_yaml_suite`
    so the wizard and the dashboard suite-creator share one code path.
    """
    from promptry.suite_builder import write_yaml_suite

    write_yaml_suite(path, suite_dict, elsewhere_hint="elsewhere with --output")


def _py_assertion_call(assertion: str, output_var: str, value: str) -> str:
    func = _CASE_ASSERTION_FUNCS[assertion]
    if assertion in ("contains", "not_contains"):
        return f"{func}({output_var}, [{value!r}])"
    return f"{func}({output_var}, {value!r})"


def _build_python_block(name, description, pipeline, model, prompt_template, grouped_cases) -> str:
    """Render a `@suite(...)`-decorated function, matching the decorator
    idiom used throughout promptry/evaluator.py and the `init` scaffold.

    Self-contained: imports `suite` (needed by the decorator, which must be
    resolvable at module-exec time) and every assert_* it uses right above
    the function, so the block is safe to append to any existing evals.py
    regardless of what that file already imports.
    """
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in name).strip("_").lower()
    func_name = f"test_{safe}" if safe else "test_suite"
    pipeline_alias = f"_pipeline_{func_name}"

    used_assertions = sorted({a for _, expects in grouped_cases for a, _ in expects})
    import_names = ["suite"] + [_CASE_ASSERTION_FUNCS[a] for a in used_assertions]

    lines: list[str] = []
    lines.append("")
    lines.append("")
    lines.append(f"from promptry import {', '.join(import_names)}")
    lines.append("from promptry.evaluator import check_all")
    lines.append("")
    lines.append(f"@suite({name!r})")
    lines.append(f"def {func_name}():")
    if description:
        lines.append(f'    """{description}"""')
    if pipeline:
        mod, _, fn = pipeline.partition(":")
        lines.append(f"    from {mod} import {fn} as {pipeline_alias}")
    else:
        lines.append("    from promptry.llm import complete as _complete")
        lines.append(f"    def {pipeline_alias}(input_val):")
        lines.append(f"        text = {prompt_template!r}.replace('{{input}}', str(input_val))")
        lines.append(f'        return _complete({model!r}, [{{"role": "user", "content": text}}])')
    lines.append("")

    output_vars = []
    for i, (inp, _expects) in enumerate(grouped_cases):
        var = f"_output_{i}"
        output_vars.append(var)
        lines.append(f"    {var} = {pipeline_alias}({inp!r})")

    lines.append("    check_all(")
    for (inp, expects), var in zip(grouped_cases, output_vars):
        for assertion, value in expects:
            lines.append(f"        lambda: {_py_assertion_call(assertion, var, value)},")
    lines.append("    )")
    lines.append("")

    return "\n".join(lines)


def _write_python_suite(path: Path, block: str) -> None:
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + block, encoding="utf-8")
    else:
        path.write_text(block.lstrip("\n"), encoding="utf-8")


@new_app.command("suite")
def new_suite_cmd(
    name: Optional[str] = typer.Option(None, "--name", help="Suite name."),
    yaml_mode: Optional[bool] = typer.Option(
        None, "--yaml/--python",
        help="Write a declarative evals.yaml suite (--yaml) or a @suite-decorated evals.py suite (--python).",
    ),
    pipeline: Optional[str] = typer.Option(None, "--pipeline", help="Existing pipeline as 'module:function'."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id for a direct model call (use with --prompt)."),
    prompt: Optional[str] = typer.Option(None, "--prompt", help="Prompt template; '{input}' is substituted (use with --model)."),
    case: List[str] = typer.Option(
        [], "--case",
        help="Repeatable. Grammar: 'input::assertion::value'. assertion one of: "
             "contains, not_contains, regex, exact, llm.",
    ),
    description: str = typer.Option("", "--description", help="Optional suite description."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Target file (default: evals.yaml or evals.py in the current directory).",
    ),
):
    """Scaffold a new eval suite, interactively or fully via flags.

    Every prompt has a flag equivalent, so a fully-flagged invocation never
    prompts: --name, --yaml/--python, --pipeline (or --model + --prompt), and
    at least one --case.

    Richer assertions (semantic, schema, levenshtein, rouge_l,
    embedding_distance, grounded, json_valid) aren't covered by --case; add
    them by hand-editing the generated YAML -- see the schema documented in
    promptry/yaml_suites.py.
    """
    if not name:
        name = typer.prompt("Suite name")
    name = name.strip()
    if not name:
        console.print("[red]Error:[/red] suite name cannot be empty.")
        raise typer.Exit(2)

    if yaml_mode is None:
        answer = typer.prompt("Mode (yaml/python)", default="yaml").strip().lower()
        if answer not in ("yaml", "python"):
            console.print(f"[red]Error:[/red] mode must be 'yaml' or 'python', got {answer!r}.")
            raise typer.Exit(2)
        yaml_mode = answer == "yaml"

    if pipeline and (model or prompt):
        console.print("[red]Error:[/red] use either --pipeline or --model/--prompt, not both.")
        raise typer.Exit(2)
    if model and not prompt:
        console.print("[red]Error:[/red] --model requires --prompt.")
        raise typer.Exit(2)
    if prompt and not model:
        console.print("[red]Error:[/red] --prompt requires --model.")
        raise typer.Exit(2)

    if not pipeline and not model:
        pipeline_input = typer.prompt(
            "Pipeline (module:function), or leave blank to call a model directly",
            default="",
        ).strip()
        if pipeline_input:
            pipeline = pipeline_input
        else:
            model = typer.prompt("Model id (e.g. gpt-4o-mini)").strip()
            prompt = typer.prompt(
                "Prompt template ('{input}' is substituted)", default="Answer: {input}",
            ).strip()
            if not model:
                console.print("[red]Error:[/red] a model id is required when no --pipeline is given.")
                raise typer.Exit(2)

    if pipeline and ":" not in pipeline:
        console.print(f"[red]Error:[/red] --pipeline must be 'module:function', got {pipeline!r}.")
        raise typer.Exit(2)

    try:
        parsed_cases = [_parse_case_flag(c) for c in case]
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)

    if not parsed_cases:
        console.print(f"Assertion menu: {', '.join(_CASE_ASSERTIONS)}")
        while True:
            input_val = typer.prompt("Case input").strip()
            if not input_val:
                console.print("[yellow]Empty input, skipping.[/yellow]")
            else:
                added_any = False
                while True:
                    assertion = typer.prompt(
                        f"Assertion ({'/'.join(_CASE_ASSERTIONS)}, blank to finish this case)",
                        default="",
                    ).strip()
                    if not assertion:
                        break
                    if assertion not in _CASE_ASSERTIONS:
                        console.print(
                            f"[red]Unknown assertion {assertion!r}.[/red] "
                            f"Choose from: {', '.join(_CASE_ASSERTIONS)}"
                        )
                        continue
                    value = typer.prompt(f"Value for {assertion}")
                    parsed_cases.append((input_val, assertion, value))
                    added_any = True
                if not added_any:
                    console.print("[yellow]Case needs at least one assertion; discarded.[/yellow]")
            if not typer.confirm("Add another case?", default=False):
                break

    if not parsed_cases:
        console.print("[red]Error:[/red] at least one case is required (--case, or add one interactively).")
        raise typer.Exit(2)

    grouped = _group_cases(parsed_cases)

    if yaml_mode:
        target = output or (Path.cwd() / "evals.yaml")
        suite_dict = _build_yaml_suite_dict(name, description, pipeline, model, prompt, grouped)
        try:
            _write_yaml_suite(target, suite_dict)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    else:
        target = output or (Path.cwd() / "evals.py")
        block = _build_python_block(name, description, pipeline, model, prompt, grouped)
        _write_python_suite(target, block)

    run_command = _run_command_for(name, target, output, yaml_mode)

    console.print(f"[green]Wrote[/green] suite '{name}' to {target}")
    console.print()
    console.print(f"Run it with: {run_command}")


def _run_command_for(name: str, target: Path, output: Optional[Path], yaml_mode: bool) -> str:
    """Build the exact `promptry run` command for the suite just written.

    A bare `promptry run <name>` only works when default suite discovery
    (--module evals, YAML fallback only if evals.py is absent) will find the
    target file, so:

    - custom --output: always needs an explicit --module (the YAML path, or
      the .py file's module name);
    - default evals.yaml while an evals.py exists in cwd (e.g. right after
      `promptry init`, which scaffolds both): discovery would import evals.py
      and miss the YAML suite, so pass --module evals.yaml explicitly.
    """
    if output is not None:
        module_arg = str(output) if yaml_mode else Path(output).stem
        return f"promptry run {name} --module {module_arg}"
    if yaml_mode and (Path.cwd() / "evals.py").is_file():
        return f"promptry run {name} --module {target.name}"
    return f"promptry run {name}"
