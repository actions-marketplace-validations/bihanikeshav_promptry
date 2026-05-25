"""PII and secret scanning for captured traces.

Opt-in trace capture stores raw request/response text, which is exactly where
an API key, a customer email, or a card number can quietly end up — and then
sit in the SQLite file and render in the dashboard. This module scans captured
text for the usual offenders and reports *redacted* findings so the dashboard
can warn without re-displaying the secret it found.

Pure-Python, regex-based — no external dependency, no network. Regex detection
trades recall for zero deps: it will miss novel formats and occasionally
false-positive (a 16-digit order number looks like a card), so card and SSN
matches are Luhn/shape-checked to cut the obvious noise. Treat this as a
tripwire, not a compliance-grade DLP scanner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PiiFinding:
    type: str        # e.g. "openai_api_key", "email"
    category: str    # "secret" | "pii"
    severity: str    # "high" | "medium" | "low"
    count: int       # how many times this type matched
    sample: str      # a redacted sample, safe to display


# (type, category, severity, pattern). Ordered most-specific first so a key
# that also looks like something generic is labelled as the key.
_PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
    ("private_key", "secret", "high", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("anthropic_api_key", "secret", "high", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_api_key", "secret", "high", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("aws_access_key", "secret", "high", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", "secret", "high", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack_token", "secret", "high", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", "secret", "high", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", "secret", "medium", re.compile(
        r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")),
    ("email", "pii", "medium", re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("us_ssn", "pii", "high", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", "pii", "high", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — filters most non-card 13–16 digit runs."""
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 16:
        return False
    total, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact(match: str) -> str:
    """Mask all but the last 4 characters, so a finding is identifiable but
    the secret/PII itself never leaves the scanner."""
    tail = match[-4:] if len(match) > 4 else ""
    return ("•" * min(8, max(4, len(match) - 4))) + tail


def scan_text(text: str | None) -> list[PiiFinding]:
    """Scan a blob of text and return redacted, de-duplicated findings."""
    if not text:
        return []
    # type -> (category, severity, set of redacted samples)
    found: dict[str, list] = {}
    for typ, category, severity, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(0)
            if typ == "credit_card" and not _luhn_ok(raw):
                continue
            entry = found.setdefault(typ, [category, severity, set()])
            entry[2].add(_redact(raw.strip()))
    out: list[PiiFinding] = []
    for typ, (category, severity, samples) in found.items():
        out.append(PiiFinding(
            type=typ, category=category, severity=severity,
            count=len(samples), sample=sorted(samples)[0],
        ))
    # secrets before pii, then by severity
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: (f.category != "secret", sev_rank.get(f.severity, 3), f.type))
    return out


def scan_invocation(input_text: str | None, output_text: str | None) -> dict:
    """Scan both sides of a captured call. Returns findings tagged by side
    plus a worst-severity summary for a quick dashboard badge."""
    sides = {"input": scan_text(input_text), "output": scan_text(output_text)}
    all_findings = sides["input"] + sides["output"]
    has_secret = any(f.category == "secret" for f in all_findings)
    worst = None
    for level in ("high", "medium", "low"):
        if any(f.severity == level for f in all_findings):
            worst = level
            break
    return {
        "input": [vars(f) for f in sides["input"]],
        "output": [vars(f) for f in sides["output"]],
        "total": len(all_findings),
        "has_secret": has_secret,
        "worst_severity": worst,
    }
