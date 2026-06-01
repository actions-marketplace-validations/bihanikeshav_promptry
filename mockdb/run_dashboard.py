"""Launch the promptry dashboard pointed at the mock Chipmunk DB.

    python -m mockdb.run_dashboard [--port 8421] [--open]

Sets PROMPTRY_DB to mockdb/chipmunk.db before importing promptry, so
the singleton storage resolves to the mock data rather than the user's
default ~/.promptry/promptry.db.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8421)
    parser.add_argument("--open", dest="open_browser", action="store_true",
                        help="Auto-open the browser when ready.")
    args = parser.parse_args()

    db_path = (Path(__file__).parent / "chipmunk.db").resolve()
    if not db_path.is_file():
        raise SystemExit(
            f"{db_path} not found. Run `python -m mockdb.build` first."
        )

    # Critical: set the env var BEFORE any promptry import resolves storage.
    os.environ["PROMPTRY_DB"] = str(db_path)

    # Now import promptry and invoke the dashboard command directly.
    from promptry.cli import dashboard_cmd

    print(f"Dashboard DB: {db_path}")
    print(f"Open:         http://localhost:{args.port}/")
    dashboard_cmd(port=args.port, no_open=(not args.open_browser), local=False)


if __name__ == "__main__":
    main()
