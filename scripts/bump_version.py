#!/usr/bin/env python3
"""One-command version bump across every manifest that hard-codes a version.

Usage
-----
    python scripts/bump_version.py 1.0.4

After this runs, the canonical version lives in exactly one place per ecosystem:

  * pyproject.toml                 -> the Python package. ``promptry.__version__``
                                      is read from the installed package metadata,
                                      so it tracks this automatically -- never edit
                                      ``promptry/__init__.py`` by hand.
  * promptry-js/package.json       -> npm.
  * vscode-extension/package.json  -> VS Code marketplace.
  * docs/*.html                    -> *offline fallback* literals only. The live
                                      docs read the version from the GitHub
                                      releases API at runtime, and the dashboard
                                      reads it from the installed package, so
                                      neither needs a bump -- these literals just
                                      keep the page sane when the API is blocked.

What it does NOT touch: CHANGELOG.md (write real release notes yourself) and git
tags (the release step creates the immutable ``vX.Y.Z`` tag and moves the
floating ``v1`` major tag).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-.a-zA-Z0-9]*)$")


def _sub(path: Path, pattern: str, repl: str, *, count: int = 0) -> int:
    """Rewrite ``path`` with ``pattern`` -> ``repl``; return substitutions made."""
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=count)
    if n:
        path.write_text(new, encoding="utf-8")
    return n


def bump(version: str) -> None:
    total = 0

    # pyproject.toml -- the single Python source of truth.
    total += _sub(ROOT / "pyproject.toml",
                  r'(?m)^version = "[^"]+"', f'version = "{version}"', count=1)

    # package.json manifests (first "version" key only).
    for rel in ("promptry-js/package.json", "vscode-extension/package.json"):
        total += _sub(ROOT / rel,
                      r'("version":\s*")[^"]+(")', rf'\g<1>{version}\g<2>', count=1)

    # docs offline fallbacks -- only the specific version elements, so we never
    # clobber unrelated version strings (e.g. the d2 SVG's data-d2-version).
    for name in ("index.html", "docs.html", "architecture.html"):
        p = ROOT / "docs" / name
        if not p.exists():
            continue
        total += _sub(p, r'(<span class="ver">)v\d+\.\d+\.\d+(</span>)',
                      rf'\g<1>v{version}\g<2>')
        total += _sub(p, r'(id="release-link"[^>]*>)v\d+\.\d+\.\d+',
                      rf'\g<1>v{version}')
        total += _sub(p, r'(Successfully installed promptry-)\d+\.\d+\.\d+',
                      rf'\g<1>{version}')

    print(f"Bumped to {version} ({total} substitutions).")
    print("Next: update CHANGELOG.md, commit, then tag "
          f"(git tag v{version} && git push origin v{version}) and move v1.")


def main() -> int:
    if len(sys.argv) != 2 or not SEMVER.match(sys.argv[1]):
        print(__doc__)
        print("error: expected a semver argument, e.g. 1.0.4", file=sys.stderr)
        return 2
    bump(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
