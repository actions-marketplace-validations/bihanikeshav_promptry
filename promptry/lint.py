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
# Preferred syntax: {{name}}.
_BRACE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def extract_variables(template: str) -> list[str]:
    """Distinct placeholder names a template expects, in first-seen order.

    Recognizes the preferred ``{{name}}`` syntax and legacy ``$name`` /
    ``${name}``, scanning left-to-right so order matches the source.
    """
    seen: list[str] = []
    combined = re.compile(_BRACE.pattern + "|" + _VALID.pattern)
    for m in combined.finditer(template or ""):
        tok = m.group(0)
        if tok.startswith("{{"):
            name = m.group(1)
        elif tok.startswith("${"):
            name = tok[2:-1]
        else:
            name = tok[1:]
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

    # 6. Secrets / PII pasted into the template itself — a real incident class
    #    (an API key in a committed prompt). Reuse the capture-side scanner.
    try:
        from promptry.pii import scan_text
        hits = scan_text(template)
        secrets = sorted({h.type for h in hits if h.category == "secret"})
        if secrets:
            findings.append({
                "level": "error",
                "message": f"Possible secret in the prompt text ({', '.join(secrets)}). "
                           f"Keep secrets in env/config, never in a prompt template.",
            })
    except Exception:  # pragma: no cover - scanner is best-effort
        pass

    # 7. Prefix-cache: a variable near the top of a long prompt means everything
    #    after it changes every call and can't be served from the provider's
    #    prompt cache. Moving inputs to the end maximizes cache hits (and $ saved).
    first = re.search(_BRACE.pattern + "|" + _VALID.pattern, template)
    body_len = len(template)
    if first is not None and body_len >= 400 and first.start() < 0.4 * body_len:
        findings.append({
            "level": "warning",
            "message": "A variable appears near the top — text after it can't be "
                       "prefix-cached. Put variable inputs at the end to maximize "
                       "cache hits (cheaper, faster).",
        })

    # 8. Injection-prone interpolation: untrusted-looking inputs spliced in with
    #    no delimiters make prompt-injection easier. Nudge toward fencing them.
    # Names that clearly denote *untrusted / retrieved* content — the real
    # injection vectors. Deliberately narrow (not "query"/"question"/"input"):
    # over-flagging every Q&A prompt trains people to ignore the warning.
    sensitive = {
        "user_input", "user_message", "untrusted_input", "external_input",
        "context", "retrieved_context", "retrieved", "document", "documents",
        "docs", "web_content", "chunk", "chunks",
    }
    has_delimiter = ("<" in template) or ("```" in template) or ('"""' in template)
    risky = [v for v in vars_in_template if v.lower() in sensitive]
    if risky and not has_delimiter:
        findings.append({
            "level": "warning",
            "message": f"Untrusted input ({', '.join(risky)}) is interpolated without "
                       f"delimiters. Wrap it in XML tags or a fenced block so injected "
                       f"instructions are harder to smuggle in.",
        })

    # 9. Duplicated instruction lines — usually an accidental copy-paste that
    #    just wastes tokens (and can contradict itself).
    seen_lines: dict[str, int] = {}
    for line in template.splitlines():
        s = line.strip()
        if len(s) >= 25:
            seen_lines[s] = seen_lines.get(s, 0) + 1
    dups = [s for s, n in seen_lines.items() if n > 1]
    if dups:
        findings.append({
            "level": "info",
            "message": f"{len(dups)} instruction line(s) are duplicated — likely a "
                       f"copy-paste; trim to save tokens.",
        })

    return findings
