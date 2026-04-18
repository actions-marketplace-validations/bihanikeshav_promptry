"""Realistic mock production dataset for promptry demos and self-testing.

Scenario: a customer-support bot for a fictional SaaS product called
'Chipmunk Analytics' — a data analytics dashboard.

Runs against an isolated DB at `mockdb/chipmunk.db` so production
promptry data isn't polluted. The build and insights scripts both
set PROMPTRY_DB automatically.

Run `python -m mockdb.build` to populate the SQLite store.
Run `python -m mockdb.insights` to print a tour of promptry's analyses.
"""
import os
from pathlib import Path

# Isolated DB for the mock data — set BEFORE any promptry imports resolve
# the storage singleton. Callers that import mockdb get this for free.
MOCK_DB_PATH = Path(__file__).parent / "chipmunk.db"
os.environ.setdefault("PROMPTRY_DB", str(MOCK_DB_PATH))
