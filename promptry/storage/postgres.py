"""Postgres scale-tier backend (ALPHA — seam only).

promptry defaults to a single SQLite file, which covers single-instance
deployments at any realistic traffic. The Postgres backend is the opt-in
*scale tier* for genuinely multi-instance / high-write deployments — selected
with storage.mode = "postgres" (or PROMPTRY_STORAGE=postgres://...), never the
default.

STATUS: the pluggable seam is in place (this class + the factory routing + the
[postgres] extra), but the full SQL port is on the roadmap — SQLiteStorage is
~2000 lines of SQLite-specific SQL (json_extract, INSERT OR IGNORE, expression
indexes, datetime('now')) that a real Postgres backend must translate, and that
work needs a Postgres instance to build and test against. Until then this raises
a clear error rather than silently misbehaving.

Multi-instance today: use storage.mode = "remote" — N app instances ship to one
promptry ingest endpoint that owns the (SQLite) store. No second database.
"""
from __future__ import annotations

import os

_NOT_IMPLEMENTED = (
    "The Postgres scale-tier backend is alpha and not yet implemented. "
    "Use the default SQLite backend (single instance, any realistic traffic), "
    "or storage.mode='remote' (the multi-instance collector: many app instances "
    "-> one ingest endpoint). The full Postgres port is on the roadmap."
)


class PostgresStorage:
    """Alpha placeholder (see module docstring). A plain class — not a
    BaseStorage subclass — so it raises this clear message rather than an
    abstract-instantiation error, until the real backend is ported."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or os.environ.get("PROMPTRY_POSTGRES_DSN")
        raise NotImplementedError(_NOT_IMPLEMENTED)
