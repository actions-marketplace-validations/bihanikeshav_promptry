#!/usr/bin/env python3
"""Refresh repo-root prices.json from litellm (when available) + bundled rates.

Used by the daily GitHub Action so the published feed at
https://raw.githubusercontent.com/bihanikeshav/promptry/main/prices.json
stays current. Clients (and the dashboard auto-refresh) pull that file —
they do not call vendor APIs directly.

Usage:
  python scripts/update_prices_feed.py
  python scripts/update_prices_feed.py --out prices.json
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
        help="Path to write the feed JSON (default: repo prices.json)",
    )
    ap.add_argument(
        "--no-litellm",
        action="store_true",
        help="Skip litellm merge; only re-export the bundled table.",
    )
    args = ap.parse_args()

    pricing.ensure_prices_loaded()
    litellm_n = 0
    if not args.no_litellm:
        litellm_n = pricing.refresh_rates_from_litellm()
        print(f"litellm models merged: {litellm_n}")

    now = datetime.now(timezone.utc)
    feed = pricing.export_feed()
    feed["version"] = now.strftime("%Y-%m-%d")
    feed["updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    feed["source"] = "litellm+bundled" if litellm_n else "bundled-export"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(feed['rates'])} rates -> {args.out}")
    print(f"version={feed['version']} source={feed['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
