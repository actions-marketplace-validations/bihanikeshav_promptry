"""Static checks for prompt templates (CMS / render_prompt).

Catches the footguns the CMS migration guide warns about before a bad
edit reaches production: stray ``$`` that string.Template will misread,
placeholders, and missing output-format guidance. Used by the dashboard
edit endpoint (warn, don't block) and a `promptry lint` CLI.
"""
from __future__ import annotations

import re
from string import Template

# Matches what string.Template treats as a substitution: $name or ${name}.
_VALID = re.compile(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)")
# Any $ occurrence (to find ones that AREN'T valid placeholders or $$).
_ANY_DOLLAR = re.compile(r"\$")


def extract_variables(template: str) -> list[str]:
    """Distinct ``$placeholder`` names a template expects, in first-seen order."""
    seen: list[str] = []
    for m in _VALID.finditer(template or ""):
        tok = m.group(0)
        name = tok[2:-1] if tok.startswith("${") else tok[1:]
        if name not in seen:
            seen.append(name)
    return seen


def lint_prompt(template: str, *, known_vars: list[str] | None = None) -> list[dict]:
    """Return a list of {level, message} findings. level ∈ {error, warning, info}.

    - error: would break string.Template.substitute / safe_substitute usage.
    - warning: likely a mistake (stray $, no output-format guidance).
    - info: stylistic / advisory.
    """
    findings: list[dict] = []
    if not template or not template.strip():
        return [{"level": "error", "message": "Prompt is empty."}]

    # 1. Stray '$' that is neither a valid placeholder nor an escaped '$$'.
    #    string.Template.safe_substitute leaves these literally, but
    #    .substitute() would raise — and it signals a typo'd placeholder.
    escaped = template.replace("$$", "")  # remove valid escapes first
    stray = 0
    for m in _ANY_DOLLAR.finditer(escaped):
        # is this $ the start of a valid placeholder?
        if not _VALID.match(escaped, m.start()):
            stray += 1
    if stray:
        findings.append({
            "level": "warning",
            "message": f"{stray} stray '$' not part of a $placeholder. "
                       f"Escape literal dollars as '$$' (e.g. prices) or fix the placeholder.",
        })

    # 2. Output-format guidance — prompts that should return JSON often forget
    #    to say "JSON only", causing parse failures downstream.
    low = template.lower()
    if ("json" in low) and ("only" not in low and "valid json" not in low):
        findings.append({
            "level": "info",
            "message": "Mentions JSON but not 'valid JSON'/'JSON only' — models may wrap output in prose.",
        })

    # 3. Placeholders present but caller-supplied set known: flag extras.
    vars_in_template = extract_variables(template)
    if known_vars is not None:
        missing = [v for v in vars_in_template if v not in known_vars]
        if missing:
            findings.append({
                "level": "warning",
                "message": f"Template uses placeholders the caller may not supply: {', '.join(missing)}.",
            })

    # 4. Very long single-line prompt (no structure).
    if "\n" not in template.strip() and len(template) > 600:
        findings.append({
            "level": "info",
            "message": "Long single-line prompt — consider line breaks/sections for readability.",
        })

    # 5. Sanity: does it even parse as a Template?
    try:
        Template(template).safe_substitute({})
    except Exception as e:  # pragma: no cover - safe_substitute rarely raises
        findings.append({"level": "error", "message": f"Template parse error: {e}"})

    return findings
