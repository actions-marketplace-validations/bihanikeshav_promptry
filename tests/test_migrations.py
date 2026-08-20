"""Tests for the lightweight schema migration system."""
import sqlite3

import pytest

from promptry.storage import Storage
from promptry.storage.sqlite import _MIGRATIONS, _SCHEMA_VERSION_DDL


class TestFreshDatabase:

    def test_all_migrations_applied(self, tmp_path):
        """A fresh DB should have all migrations applied."""
        db = Storage(db_path=tmp_path / "fresh.db")
        try:
            conn = db._conn
            cur = conn.execute("SELECT version, description FROM schema_version ORDER BY version")
            rows = cur.fetchall()
            assert len(rows) == len(_MIGRATIONS)
            for row, (expected_ver, expected_desc, _) in zip(rows, _MIGRATIONS):
                assert row["version"] == expected_ver
                assert row["description"] == expected_desc
        finally:
            db.close()

    def test_schema_version_has_applied_at(self, tmp_path):
        """Each migration row should have an applied_at timestamp."""
        db = Storage(db_path=tmp_path / "fresh.db")
        try:
            cur = db._conn.execute("SELECT applied_at FROM schema_version")
            for row in cur.fetchall():
                assert row["applied_at"] is not None
        finally:
            db.close()

    def test_tables_exist(self, tmp_path):
        """All expected tables should exist after migration."""
        db = Storage(db_path=tmp_path / "fresh.db")
        try:
            cur = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row["name"] for row in cur.fetchall()}
            expected = {"prompts", "prompt_tags", "eval_runs", "eval_results",
                        "votes", "schema_version"}
            assert expected.issubset(tables)
        finally:
            db.close()


class TestExistingDatabase:

    def _create_legacy_db(self, db_path):
        """Simulate a pre-migration database that already has the tables
        but no schema_version table."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                hash TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(name, version),
                UNIQUE(name, hash)
            );
            CREATE TABLE prompt_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id),
                UNIQUE(prompt_id, tag)
            );
            CREATE TABLE eval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suite_name TEXT NOT NULL,
                prompt_name TEXT,
                prompt_version INTEGER,
                model_version TEXT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                overall_pass INTEGER NOT NULL DEFAULT 1,
                overall_score REAL
            );
            CREATE TABLE eval_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                test_name TEXT NOT NULL,
                assertion_type TEXT NOT NULL,
                passed INTEGER NOT NULL,
                score REAL,
                details TEXT,
                latency_ms REAL,
                FOREIGN KEY (run_id) REFERENCES eval_runs(id)
            );
            CREATE TABLE votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_name TEXT NOT NULL,
                prompt_version INTEGER,
                response TEXT NOT NULL,
                score INTEGER NOT NULL,
                message TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO prompts (name, version, content, hash) VALUES ('legacy', 1, 'old content', 'oldhash');
        """)
        conn.commit()
        conn.close()

    def test_legacy_db_gets_migrated(self, tmp_path):
        """An existing DB (tables present, no schema_version) should be
        migrated without losing data."""
        db_path = tmp_path / "legacy.db"
        self._create_legacy_db(db_path)

        db = Storage(db_path=db_path)
        try:
            # schema_version should now exist and show version 1
            cur = db._conn.execute("SELECT MAX(version) FROM schema_version")
            assert cur.fetchone()[0] == len(_MIGRATIONS)

            # existing data should be intact
            prompt = db.get_prompt("legacy", 1)
            assert prompt is not None
            assert prompt.content == "old content"
        finally:
            db.close()

    def test_legacy_db_indexes_created(self, tmp_path):
        """Indexes should be created on the legacy DB."""
        db_path = tmp_path / "legacy.db"
        self._create_legacy_db(db_path)

        db = Storage(db_path=db_path)
        try:
            cur = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            )
            indexes = {row["name"] for row in cur.fetchall()}
            assert "idx_prompts_name" in indexes
            assert "idx_votes_prompt" in indexes
        finally:
            db.close()


class TestInvocationTypedColumns:
    """Migration that adds typed metric columns to invocations + backfill."""

    def _create_pre_typed_db(self, db_path):
        """A DB at the schema version just before typed columns were added:
        invocations exists with JSON-only metadata, no cost/tokens_in/... cols."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_SCHEMA_VERSION_DDL)
        # Build the invocations table as it was through migration 5, plus the
        # budgets/golden tables, and mark every migration BEFORE the new typed
        # one as already applied so only the new migration runs on open.
        conn.executescript(
            """
            CREATE TABLE invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_name TEXT NOT NULL,
                prompt_version INTEGER,
                metadata TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                input_text TEXT,
                output_text TEXT,
                request_id TEXT
            );
            """
        )
        # Pre-apply every migration up to (but excluding) the typed-columns
        # migration, located by content so later additions to _MIGRATIONS don't
        # break this fixture.
        typed_ver = next(v for v, _d, stmts in _MIGRATIONS
                         if any("ADD COLUMN cost" in s for s in stmts))
        prior = [m for m in _MIGRATIONS if m[0] < typed_ver]
        for version, desc, _ in prior:
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, desc),
            )
        rows = [
            ("p.a", '{"tokens_in": 500, "tokens_out": 100, "model": "gpt-4o", "cost": 0.005, "latency_ms": 120.0}'),
            ("p.a", '{"prompt_tokens": 300, "completion_tokens": 50, "model": "gpt-4o", "cost": 0.003}'),
            ("p.b", '{"input_tokens": 250, "output_tokens": 40, "model": "claude", "cost": 0.002, "latency_ms": 90.0}'),
            ("p.c", None),
        ]
        for name, meta in rows:
            conn.execute(
                "INSERT INTO invocations (prompt_name, metadata) VALUES (?, ?)",
                (name, meta),
            )
        conn.commit()
        conn.close()

    def test_typed_columns_added_and_backfilled(self, tmp_path):
        db_path = tmp_path / "pretyped.db"
        self._create_pre_typed_db(db_path)

        db = Storage(db_path=db_path)
        try:
            cur = db._conn.execute("SELECT MAX(version) FROM schema_version")
            assert cur.fetchone()[0] == len(_MIGRATIONS)

            cols = {r[1] for r in db._conn.execute("PRAGMA table_info(invocations)")}
            for c in ("cost", "tokens_in", "tokens_out", "model", "latency_ms"):
                assert c in cols

            cur = db._conn.execute(
                "SELECT prompt_name, cost, tokens_in, tokens_out, model, latency_ms "
                "FROM invocations ORDER BY id"
            )
            got = [tuple(r) for r in cur.fetchall()]
            assert got[0] == ("p.a", 0.005, 500, 100, "gpt-4o", 120.0)
            # OpenAI-style spellings backfill into the typed columns
            assert got[1] == ("p.a", 0.003, 300, 50, "gpt-4o", None)
            # Anthropic-style spellings backfill too
            assert got[2] == ("p.b", 0.002, 250, 40, "claude", 90.0)
            # JSON-null metadata row stays null across the board
            assert got[3] == ("p.c", None, None, None, None, None)

            # Indexes created
            idx = {r[0] for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_invocations%'"
            )}
            assert "idx_invocations_model_created" in idx
            assert "idx_invocations_created_cost" in idx
        finally:
            db.close()

    def _create_pre_typed_db_with_bad_metadata(self, db_path):
        """Like _create_pre_typed_db but the only invocations carry metadata
        that is NOT valid JSON: a plain non-JSON string and an empty string.
        Old readers tolerated these (they swallowed json.loads errors); the
        backfill's json_extract would raise on them, and because the ALTERs
        commit before the failing UPDATE, a raise would leave a DB that errors
        on every subsequent open (ALTER has no IF NOT EXISTS)."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_SCHEMA_VERSION_DDL)
        conn.executescript(
            """
            CREATE TABLE invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_name TEXT NOT NULL,
                prompt_version INTEGER,
                metadata TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                input_text TEXT,
                output_text TEXT,
                request_id TEXT
            );
            """
        )
        typed_ver = next(v for v, _d, stmts in _MIGRATIONS
                         if any("ADD COLUMN cost" in s for s in stmts))
        for version, desc, _ in [m for m in _MIGRATIONS if m[0] < typed_ver]:
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, desc),
            )
        for name, meta in [("bad.a", "not json"), ("bad.b", "")]:
            conn.execute(
                "INSERT INTO invocations (prompt_name, metadata) VALUES (?, ?)",
                (name, meta),
            )
        conn.commit()
        conn.close()

    def test_backfill_survives_malformed_metadata(self, tmp_path):
        """Migration must not raise on malformed/empty-string metadata; those
        rows get NULL typed columns and aggregates ignore them."""
        db_path = tmp_path / "badmeta.db"
        self._create_pre_typed_db_with_bad_metadata(db_path)

        # Must not raise (a raise here would also poison every reopen).
        db = Storage(db_path=db_path)
        try:
            assert db._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == len(_MIGRATIONS)
            got = [tuple(r) for r in db._conn.execute(
                "SELECT prompt_name, cost, tokens_in, tokens_out, model, latency_ms "
                "FROM invocations ORDER BY id")]
            assert got == [("bad.a", None, None, None, None, None),
                           ("bad.b", None, None, None, None, None)]
            # Aggregates ignore the malformed rows entirely.
            data = db.get_cost_data(days=3650)
            assert data["summary"]["total_cost"] == pytest.approx(0.0)
            assert data["summary"]["total_tokens_in"] == 0
        finally:
            db.close()

        # Reopening a second time must also succeed (proves no half-applied,
        # error-on-open state was committed).
        db2 = Storage(db_path=db_path)
        db2.close()

    def test_aggregates_unchanged_after_backfill(self, tmp_path):
        """Cost aggregates read the same before/after the typed-column path."""
        db_path = tmp_path / "pretyped2.db"
        self._create_pre_typed_db(db_path)
        db = Storage(db_path=db_path)
        try:
            data = db.get_cost_data(days=3650)
            assert data["summary"]["total_calls"] == 4
            assert data["summary"]["total_cost"] == pytest.approx(0.01)
            assert data["summary"]["total_tokens_in"] == 1050
            assert data["summary"]["total_tokens_out"] == 190
        finally:
            db.close()


class TestIdempotency:

    def test_migrations_idempotent(self, tmp_path):
        """Running migrations twice should not fail or duplicate rows."""
        db_path = tmp_path / "idem.db"
        db = Storage(db_path=db_path)
        db.save_prompt("test", "hello", "h1")
        db.close()

        # Open again -- _init_schema runs migrations again
        db2 = Storage(db_path=db_path)
        try:
            cur = db2._conn.execute("SELECT COUNT(*) FROM schema_version")
            assert cur.fetchone()[0] == len(_MIGRATIONS)

            # data should still be there
            prompt = db2.get_prompt("test", 1)
            assert prompt is not None
            assert prompt.content == "hello"
        finally:
            db2.close()

    def test_reopen_no_extra_rows(self, tmp_path):
        """Opening the DB multiple times should not add extra schema_version rows."""
        db_path = tmp_path / "multi.db"
        for _ in range(3):
            db = Storage(db_path=db_path)
            db.close()

        db = Storage(db_path=db_path)
        try:
            cur = db._conn.execute("SELECT COUNT(*) FROM schema_version")
            assert cur.fetchone()[0] == len(_MIGRATIONS)
        finally:
            db.close()
