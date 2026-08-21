"""Postgres scale-tier backend (opt-in).

promptry defaults to a single SQLite file. This is the opt-in *scale tier* for
multi-instance / high-write deployments — selected with storage.mode="postgres"
(DSN from PROMPTRY_POSTGRES_DSN or [storage] endpoint), never the default.

Implementation: rather than maintain a second 2000-line copy of every query,
PostgresStorage subclasses SQLiteStorage and overrides only the connection
(psycopg, with a small bounded SQL-dialect translator) and schema DDL. All the
query methods are inherited and run unchanged; the translator maps the handful
of SQLite idioms they use to Postgres:

  ?  ->  %s                          (placeholder)
  literal %  ->  %%                  (so LIKE wildcards survive psycopg)
  bool params -> int                 (our INTEGER 0/1 columns)
  INSERT OR IGNORE -> ON CONFLICT DO NOTHING
  plain INSERT -> ... RETURNING id   (emulates sqlite3 lastrowid)
  json_extract(x,'$.k') -> (x::jsonb ->> 'k')
  json_valid(x) -> (x IS JSON)       (Postgres 16+)
  datetime('now', expr) -> to_char(now() + (expr)::interval, 'YYYY-MM-DD HH24:MI:SS')

created_at stays TEXT (as in SQLite) so every comparison/LIKE has identical
semantics across both backends.
"""
from __future__ import annotations

import json
import os
import re
import threading

from promptry.storage.sqlite import SQLiteStorage

# Final schema (all 14 tables at their latest shape) — created directly, so we
# don't replay the SQLite-dialect migrations. created_at is TEXT to match SQLite.
_TS_DEFAULT = "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')"
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY, description TEXT,
        applied_at TEXT DEFAULT (%s))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS prompts (
        id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
        content TEXT NOT NULL, hash TEXT NOT NULL, metadata TEXT,
        created_at TEXT NOT NULL DEFAULT (%s),
        UNIQUE(name, version), UNIQUE(name, hash))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS prompt_tags (
        id BIGSERIAL PRIMARY KEY, prompt_id INTEGER NOT NULL REFERENCES prompts(id),
        tag TEXT NOT NULL, UNIQUE(prompt_id, tag))""",
    """CREATE TABLE IF NOT EXISTS eval_runs (
        id BIGSERIAL PRIMARY KEY, suite_name TEXT NOT NULL, prompt_name TEXT,
        prompt_version INTEGER, model_version TEXT,
        timestamp TEXT NOT NULL DEFAULT (%s),
        overall_pass INTEGER NOT NULL DEFAULT 1, overall_score REAL)""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS eval_results (
        id BIGSERIAL PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES eval_runs(id),
        test_name TEXT NOT NULL, assertion_type TEXT NOT NULL, passed INTEGER NOT NULL,
        score REAL, details TEXT, latency_ms REAL)""",
    """CREATE TABLE IF NOT EXISTS votes (
        id BIGSERIAL PRIMARY KEY, prompt_name TEXT NOT NULL, prompt_version INTEGER,
        response TEXT NOT NULL, score INTEGER NOT NULL, message TEXT, metadata TEXT,
        created_at TEXT NOT NULL DEFAULT (%s))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS datasets (
        id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
        items TEXT NOT NULL, metadata TEXT, created_at TEXT DEFAULT (%s),
        UNIQUE(name, version))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS invocations (
        id BIGSERIAL PRIMARY KEY, prompt_name TEXT NOT NULL, prompt_version INTEGER,
        metadata TEXT, created_at TEXT NOT NULL DEFAULT (%s),
        input_text TEXT, output_text TEXT, request_id TEXT, cost REAL,
        tokens_in INTEGER, tokens_out INTEGER, model TEXT, latency_ms REAL,
        response_id TEXT)""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS feedback (
        id BIGSERIAL PRIMARY KEY, request_id TEXT, prompt_name TEXT, rating REAL,
        comment TEXT, source TEXT, created_at TEXT NOT NULL DEFAULT (%s))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS golden_examples (
        id BIGSERIAL PRIMARY KEY, prompt_name TEXT NOT NULL, input_text TEXT NOT NULL,
        reference_output TEXT, source_invocation_id INTEGER, model TEXT,
        created_at TEXT NOT NULL DEFAULT (%s))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS budgets (
        id BIGSERIAL PRIMARY KEY, scope TEXT NOT NULL, target TEXT, period TEXT NOT NULL,
        limit_usd REAL NOT NULL, created_at TEXT NOT NULL DEFAULT (%s))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT,
        password_hash TEXT, role TEXT NOT NULL DEFAULT 'viewer',
        is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (%s),
        last_login_at TEXT, token_version INTEGER NOT NULL DEFAULT 0)""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS user_identities (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, subject TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (%s), UNIQUE(provider, subject))""" % _TS_DEFAULT,
    """CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY, ts TEXT NOT NULL DEFAULT (%s), actor TEXT,
        actor_id INTEGER, action TEXT NOT NULL, target TEXT, ip TEXT,
        result TEXT NOT NULL DEFAULT 'ok', detail TEXT)""" % _TS_DEFAULT,
]
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name)",
    "CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)",
    "CREATE INDEX IF NOT EXISTS idx_prompt_tags_tag ON prompt_tags(tag)",
    "CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON eval_runs(suite_name)",
    "CREATE INDEX IF NOT EXISTS idx_eval_runs_model ON eval_runs(model_version)",
    "CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_votes_prompt ON votes(prompt_name)",
    "CREATE INDEX IF NOT EXISTS idx_votes_created ON votes(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_datasets_name ON datasets(name)",
    "CREATE INDEX IF NOT EXISTS idx_invocations_name ON invocations(prompt_name)",
    "CREATE INDEX IF NOT EXISTS idx_invocations_created ON invocations(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_invocations_name_created ON invocations(prompt_name, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_eval_runs_suite_time ON eval_runs(suite_name, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_invocations_request ON invocations(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_invocations_model_created ON invocations(model, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_invocations_created_cost ON invocations(created_at, cost)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_invocations_response_uq "
    "ON invocations(response_id) WHERE response_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_invocations_trace "
    "ON invocations((metadata::jsonb ->> 'trace_id')) WHERE (metadata IS JSON)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_request ON feedback(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_prompt ON feedback(prompt_name)",
    "CREATE INDEX IF NOT EXISTS idx_golden_prompt ON golden_examples(prompt_name)",
    "CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)",
]

_SENTINEL = "\x00P\x00"


class _Row(dict):
    """Behaves like sqlite3.Row: supports both row["col"] and row[0], and
    dict(row). The inherited SQLiteStorage code uses both access styles."""

    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    cols = [d.name for d in cursor.description] if cursor.description else []

    def make(values):
        return _Row(cols, values)
    return make


_NUMERIC_JSON_KEYS = {"cost", "latency_ms", "prompt_tokens", "completion_tokens"}


def _json_repl(m: "re.Match") -> str:
    col, key = m.group(1), m.group(2)
    expr = f"({col}::jsonb ->> '{key}')"
    # jsonb ->> is text; numeric keys used in SUM/COALESCE/CAST need a cast,
    # while text keys (trace_id, span_name, api, status) must stay text.
    if key in _NUMERIC_JSON_KEYS or key.endswith("_tokens"):
        return f"{expr}::numeric"
    return expr


def _translate(sql: str) -> tuple[str, bool]:
    """SQLite SQL -> Postgres. Returns (sql, appended_returning_id)."""
    s = sql
    s = re.sub(r"json_extract\(\s*([A-Za-z_][\w.]*)\s*,\s*'\$\.([\w]+)'\s*\)",
               _json_repl, s)
    s = re.sub(r"json_valid\(\s*([A-Za-z_][\w.]*)\s*\)", r"(\1 IS JSON)", s)
    s = re.sub(r"datetime\('now',\s*([^)]+)\)",
               r"to_char(now() + (\1)::interval, 'YYYY-MM-DD HH24:MI:SS')", s)
    s = re.sub(r"datetime\('now'\)", "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')", s)
    # GROUP_CONCAT([DISTINCT] x) -> string_agg([DISTINCT] x, ',')
    s = re.sub(r"GROUP_CONCAT\(\s*(DISTINCT\s+)?(.+?)\s*\)",
               r"string_agg(\1\2, ',')", s, flags=re.I)
    s = re.sub(r"\bIFNULL\(", "COALESCE(", s, flags=re.I)

    appended = False
    if re.search(r"\bINSERT\s+OR\s+IGNORE\b", s, re.I):
        s = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", s, flags=re.I)
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if re.match(r"\s*INSERT\b", s, re.I) and "RETURNING" not in s.upper() \
            and "schema_version" not in s.lower():
        s = s.rstrip().rstrip(";") + " RETURNING id"
        appended = True

    # Protect our placeholders, escape literal % (LIKE wildcards), restore.
    s = s.replace("?", _SENTINEL)
    s = s.replace("%", "%%")
    s = s.replace(_SENTINEL, "%s")
    return s, appended


class _PgCursor:
    def __init__(self, real):
        self._cur = real
        self.lastrowid = None

    def execute(self, sql, params=()):
        params = tuple(int(p) if isinstance(p, bool) else p for p in (params or ()))
        translated, ret = _translate(sql)
        self._cur.execute(translated, params)
        self.lastrowid = None
        if ret:
            try:
                row = self._cur.fetchone()
                if row is not None:
                    self.lastrowid = row["id"] if isinstance(row, dict) else row[0]
            except Exception:
                pass
        return self

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)


class _PgConn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _PgCursor(self._conn.cursor()).execute(sql, params)

    def cursor(self):
        return _PgCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def raw(self, sql, params=()):
        """Run PG-native SQL without dialect translation (for schema setup)."""
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql):
        for stmt in (x for x in sql.split(";") if x.strip()):
            self._conn.cursor().execute(_translate(stmt)[0])
        self._conn.commit()


class PostgresStorage(SQLiteStorage):
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or os.environ.get("PROMPTRY_POSTGRES_DSN")
        if not self._dsn:
            raise ValueError(
                "PostgresStorage needs a DSN (PROMPTRY_POSTGRES_DSN or [storage] endpoint)")
        self._db_path = "postgres"  # health endpoint reads this
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._init_schema()
        # Be honest about what this tier currently is: a correctness-first ALPHA
        # that uses ONE shared connection behind a global lock, so every query is
        # serialized. It gives durability/multi-instance-schema, not concurrency
        # — it is not yet faster than SQLite under load. Connection pooling is
        # tracked separately; until it lands, don't advertise a scale win.
        import logging
        logging.getLogger("promptry").warning(
            "Postgres storage is ALPHA and currently single-connection "
            "(serialized); it provides durability, not concurrency. Do not "
            "expect a throughput gain over SQLite yet."
        )

    def _connect(self):
        import psycopg
        conn = psycopg.connect(self._dsn, autocommit=True, row_factory=_row_factory)
        return _PgConn(conn)

    def _init_schema(self):
        # These statements are already PG-native — run them raw (no translation).
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.raw(stmt)
            for stmt in _INDEXES:
                self._conn.raw(stmt)
            from promptry.storage.sqlite import _MIGRATIONS
            latest = max(v for v, _d, _s in _MIGRATIONS)
            cur = self._conn.raw("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version")
            if cur.fetchone()["v"] < latest:
                self._conn.raw(
                    "INSERT INTO schema_version (version, description) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING", (latest, "postgres initial schema"))

    def save_eval_run_atomic(
        self, *, results, suite_name, prompt_name=None, prompt_version=None,
        model_version=None, overall_pass=True, overall_score=None,
    ) -> int:
        """Postgres override for atomicity. The shared connection runs in
        autocommit mode (so reads don't leave idle-in-transaction snapshots),
        which makes the inherited commit()/rollback() no-ops — a mid-write
        failure would leave a committed run row with partial/no results. Run
        both inserts inside one explicit psycopg transaction instead, so the
        run and all its result rows land together or not at all."""
        with self._lock:
            raw = self._conn._conn  # underlying psycopg connection
            with raw.transaction():
                cur = self._conn.execute(
                    "INSERT INTO eval_runs (suite_name, prompt_name, prompt_version, "
                    "model_version, overall_pass, overall_score) VALUES (?, ?, ?, ?, ?, ?)",
                    (suite_name, prompt_name, prompt_version, model_version,
                     int(overall_pass), overall_score),
                )
                run_id = cur.lastrowid
                for r in results:
                    details_json = json.dumps(r["details"]) if r.get("details") else None
                    self._conn.execute(
                        "INSERT INTO eval_results (run_id, test_name, assertion_type, "
                        "passed, score, details, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (run_id, r["test_name"], r["assertion_type"], int(r["passed"]),
                         r.get("score"), details_json, r.get("latency_ms")),
                    )
            return run_id
