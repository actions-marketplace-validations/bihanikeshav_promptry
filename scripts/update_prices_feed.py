#!/usr/bin/env python3
"""Build prices.json from LiteLLM only — no hand-maintained rate table.

Writes:
  - prices.json                 (repo root; published GitHub feed)
  - promptry/data/prices.json   (packaged snapshot for offline installs)

Usage:
  pip install 'promptry[llm]'   # or: pip install litellm
  python scripts/update_prices_feed.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from promptry import pricing  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "prices.json",
        help="Primary feed path (default: repo prices.json)",
    )
    ap.add_argument(
        "--package-out",
        type=Path,
        default=ROOT / "promptry" / "data" / "prices.json",
        help="Packaged snapshot path",
    )
    args = ap.parse_args()

    # Fresh import path: ignore any previously loaded package/user feed
    pricing._prices_loaded = True  # skip ensure re-load mid-script
    pricing.RATES.clear()
    pricing._recompute_rate_indexes()

    n = pricing.refresh_rates_from_litellm(replace=True)
    if n == 0:
        print(
            "ERROR: litellm produced 0 rates. Install with:\n"
            "  pip install litellm\n"
            "  # or: pip install 'promptry[llm]'",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(timezone.utc)
    # Reroutes are NOT from litellm — keep them out of the published catalog.
    feed = {
        "version": now.strftime("%Y-%m-%d"),
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "litellm",
        "mode": "replace",
        "rates": {k: dict(v) for k, v in sorted(pricing.RATES.items())},
        "reroutes": {},
    }

    text = json.dumps(feed, indent=2) + "\n"
    for path in {args.out, args.package_out}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {len(feed['rates'])} rates -> {path}")

    print(f"version={feed['version']} source=litellm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
