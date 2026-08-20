"""SQLite storage backend.

Everything that touches the database lives here. Other modules go through
the Storage interface instead of importing sqlite3 directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from promptry.config import get_config
from promptry.models import PromptRecord, EvalRunRecord, EvalResultRecord
from promptry.storage.base import BaseStorage


def _db_encryption_key() -> str | None:
    """Encryption passphrase from PROMPTRY_DB_KEY, or None for a plain DB."""
    key = (os.environ.get("PROMPTRY_DB_KEY") or "").strip()
    return key or None


def _sqlcipher_module():
    """A sqlite3-compatible DBAPI module that supports SQLCipher encryption, or
    None if no such driver is installed."""
    for mod_name in ("sqlcipher3.dbapi2", "pysqlcipher3.dbapi2"):
        try:
            import importlib
            return importlib.import_module(mod_name)
        except ImportError:
            continue
    return None

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    description TEXT,
    applied_at TEXT DEFAULT (datetime('now'))
)
"""

# Each entry: (version, description, list_of_sql_statements)
# Migration 1 uses CREATE TABLE IF NOT EXISTS so it works on both fresh and
# pre-existing databases (which already have the tables but no schema_version).
_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (1, "initial schema", [
        """CREATE TABLE IF NOT EXISTS prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            content TEXT NOT NULL,
            hash TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(name, version),
            UNIQUE(name, hash)
        )""",
        """CREATE TABLE IF NOT EXISTS prompt_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (prompt_id) REFERENCES prompts(id),
            UNIQUE(prompt_id, tag)
        )""",
        """CREATE TABLE IF NOT EXISTS eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_name TEXT NOT NULL,
            prompt_name TEXT,
            prompt_version INTEGER,
            model_version TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            overall_pass INTEGER NOT NULL DEFAULT 1,
            overall_score REAL
        )""",
        """CREATE TABLE IF NOT EXISTS eval_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            test_name TEXT NOT NULL,
            assertion_type TEXT NOT NULL,
            passed INTEGER NOT NULL,
            score REAL,
            details TEXT,
            latency_ms REAL,
            FOREIGN KEY (run_id) REFERENCES eval_runs(id)
        )""",
        """CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_name TEXT NOT NULL,
            prompt_version INTEGER,
            response TEXT NOT NULL,
            score INTEGER NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name)",
        "CREATE INDEX IF NOT EXISTS idx_prompts_hash ON prompts(hash)",
        "CREATE INDEX IF NOT EXISTS idx_prompt_tags_tag ON prompt_tags(tag)",
        "CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON eval_runs(suite_name)",
        "CREATE INDEX IF NOT EXISTS idx_eval_runs_model ON eval_runs(model_version)",
        "CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_votes_prompt ON votes(prompt_name)",
        "CREATE INDEX IF NOT EXISTS idx_votes_created ON votes(created_at)",
    ]),
    (2, "add datasets table", [
        """CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            items TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(name, version)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_datasets_name ON datasets(name)",
    ]),
    (3, "add invocations table for per-call metrics", [
        # Per-call ledger, distinct from prompts (which versions content).
        # Every LLM invocation gets one row, even if the rendered prompt
        # is byte-identical to a previous call. This is the right shape
        # for cost/latency dashboards.
        """CREATE TABLE IF NOT EXISTS invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_name TEXT NOT NULL,
            prompt_version INTEGER,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_invocations_name ON invocations(prompt_name)",
        "CREATE INDEX IF NOT EXISTS idx_invocations_created ON invocations(created_at)",
    ]),
    (4, "add captured input/output text to invocations", [
        # Optional, sampled full request/response text for a trace viewer.
        # Only populated when the caller opts in (capture=True), so the
        # ledger stays lean by default.
        "ALTER TABLE invocations ADD COLUMN input_text TEXT",
        "ALTER TABLE invocations ADD COLUMN output_text TEXT",
    ]),
    (5, "add request_id to invocations + feedback table", [
        # Host-app-supplied correlation id so end-user ratings/feedback can
        # be tied back to the exact invocation that produced a response.
        "ALTER TABLE invocations ADD COLUMN request_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_invocations_request ON invocations(request_id)",
        """CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            prompt_name TEXT,
            rating REAL,
            comment TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_feedback_request ON feedback(request_id)",
        "CREATE INDEX IF NOT EXISTS idx_feedback_prompt ON feedback(prompt_name)",
    ]),
    (6, "add budgets table", [
        """CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,            -- global | module | prompt
            target TEXT,                    -- module/prompt name (null for global)
            period TEXT NOT NULL,           -- daily | monthly
            limit_usd REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    ]),
    (7, "add golden_examples (eval-from-trace) table", [
        # A per-prompt golden set: real production traces promoted into
        # regression examples. The recorded output becomes the reference a
        # re-run is scored against. Distinct from code @suites (which are
        # Python) — this is the data-driven, dashboard-managed counterpart.
        """CREATE TABLE IF NOT EXISTS golden_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_name TEXT NOT NULL,
            input_text TEXT NOT NULL,
            reference_output TEXT,
            source_invocation_id INTEGER,
            model TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_golden_prompt ON golden_examples(prompt_name)",
    ]),
    (8, "typed metric columns on invocations + backfill + indexes", [
        # Promote the hot cost/latency fields out of the JSON metadata blob
        # into real columns so cost/stats/budget readers aggregate in SQL
        # (SUM/COUNT/AVG/GROUP BY) instead of a Python full scan with a
        # json.loads per row. metadata stays for full fidelity + rare fields
        # (cached_tokens, cache_write_tokens) that the readers still json_extract.
        "ALTER TABLE invocations ADD COLUMN cost REAL",
        "ALTER TABLE invocations ADD COLUMN tokens_in INTEGER",
        "ALTER TABLE invocations ADD COLUMN tokens_out INTEGER",
        "ALTER TABLE invocations ADD COLUMN model TEXT",
        "ALTER TABLE invocations ADD COLUMN latency_ms REAL",
        # Backfill from metadata, accepting the three token spellings seen in
        # the wild (promptry / Anthropic / OpenAI), matching get_cost_data.
        """UPDATE invocations SET
               cost = json_extract(metadata, '$.cost'),
               tokens_in = COALESCE(json_extract(metadata, '$.tokens_in'),
                                    json_extract(metadata, '$.input_tokens'),
                                    json_extract(metadata, '$.prompt_tokens')),
               tokens_out = COALESCE(json_extract(metadata, '$.tokens_out'),
                                     json_extract(metadata, '$.output_tokens'),
                                     json_extract(metadata, '$.completion_tokens')),
               model = json_extract(metadata, '$.model'),
               latency_ms = json_extract(metadata, '$.latency_ms')
           WHERE metadata IS NOT NULL AND json_valid(metadata)""",
        "CREATE INDEX IF NOT EXISTS idx_invocations_model_created ON invocations(model, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_invocations_created_cost ON invocations(created_at, cost)",
    ]),
    (9, "multi-user identity (users + oidc identities) and audit log", [
        # Local users. password_hash is NULL for OIDC-only accounts. email is
        # stored already-lowercased so the UNIQUE constraint is case-folded.
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            password_hash TEXT,
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_login_at TEXT
        )""",
        # External identities (OIDC). One user may link several providers.
        """CREATE TABLE IF NOT EXISTS user_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            subject TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(provider, subject)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_user_identities_user ON user_identities(user_id)",
        # Append-only audit trail. Never updated or deleted by app code.
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            target TEXT,
            ip TEXT,
            result TEXT NOT NULL DEFAULT 'ok',
            detail TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
        "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)",
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)",
    ]),
    (10, "response_id on invocations for cross-layer capture dedup", [
        # The provider's response id (e.g. chatcmpl-...). When several capture
        # layers see the same call (the LiteLLM callback AND an instrumented
        # OpenAI client), they carry the same id, so a partial UNIQUE index +
        # INSERT OR IGNORE keeps exactly one row. Partial (WHERE NOT NULL) so the
        # many rows without a captured id don't collide.
        "ALTER TABLE invocations ADD COLUMN response_id TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_invocations_response_uq "
        "ON invocations(response_id) WHERE response_id IS NOT NULL",
    ]),
]


def _metric_columns_from_metadata(metadata: dict | None) -> tuple:
    """Derive the typed invocation columns (cost, tokens_in, tokens_out,
    model, latency_ms) from a metadata dict, using the same token spellings
    and precedence as the migration-8 backfill so stored and backfilled rows
    are identical."""
    meta = metadata or {}

    def _first(*keys):
        for k in keys:
            v = meta.get(k)
            if v is not None:
                return v
        return None

    cost = meta.get("cost")
    tokens_in = _first("tokens_in", "input_tokens", "prompt_tokens")
    tokens_out = _first("tokens_out", "output_tokens", "completion_tokens")
    model = meta.get("model")
    latency_ms = meta.get("latency_ms")
    return (
        float(cost) if cost is not None else None,
        int(tokens_in) if tokens_in is not None else None,
        int(tokens_out) if tokens_out is not None else None,
        model,
        float(latency_ms) if latency_ms is not None else None,
    )


class SQLiteStorage(BaseStorage):

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = get_config().storage.db_path
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        # Encryption at rest (opt-in): when PROMPTRY_DB_KEY is set, connect via a
        # SQLCipher driver and unlock with PRAGMA key before any other access.
        # When unset (the default), use stdlib sqlite3 exactly as before — so
        # the plain-DB path is completely unchanged.
        key = _db_encryption_key()
        if key:
            driver = _sqlcipher_module()
            if driver is None:
                raise RuntimeError(
                    "PROMPTRY_DB_KEY is set but no SQLCipher driver is installed. "
                    "Install one with:  pip install 'promptry[encryption]'"
                )
            conn = driver.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA key = ?", (key,))  # must precede all other I/O
            row_type = driver.Row
        else:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            row_type = sqlite3.Row
        # Set busy_timeout FIRST so any later pragma/statement that meets a
        # transient writer lock (WAL checkpoint, a second connection in
        # async/remote mode) waits and retries instead of failing outright
        # with "database is locked".
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = row_type
        return conn

    def _init_schema(self):
        self._conn.executescript(_SCHEMA_VERSION_DDL)
        self._conn.commit()
        self._run_migrations()

    def _get_current_version(self) -> int:
        cur = self._conn.execute(
            "SELECT MAX(version) FROM schema_version"
        )
        row = cur.fetchone()
        return row[0] if row[0] is not None else 0

    def _run_migrations(self):
        current = self._get_current_version()
        for version, description, statements in _MIGRATIONS:
            if version <= current:
                continue
            for sql in statements:
                self._conn.execute(sql)
            self._conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ---- prompts ----

    def save_prompt(self, name, content, content_hash, metadata=None, force=False) -> PromptRecord:
        with self._lock:
            cur = self._conn.cursor()

            if force:
                # Explicit edit (e.g. dashboard): always become the latest
                # version, even if the content matches an OLDER version
                # (reverts must stick). Only no-op when identical to the
                # CURRENT latest, to avoid pointless duplicate versions.
                cur.execute(
                    "SELECT * FROM prompts WHERE name = ? ORDER BY version DESC LIMIT 1",
                    (name,),
                )
                latest = cur.fetchone()
                if latest and latest["hash"] == content_hash:
                    record = self._row_to_prompt(latest)
                    record.tags = self._get_tags_unlocked(cur, record.id)
                    return record
                # The UNIQUE(name, hash) constraint forbids two versions with
                # identical content. To re-promote older content (a revert),
                # drop that stale row first, then insert it as the new latest.
                cur.execute(
                    "DELETE FROM prompts WHERE name = ? AND hash = ?",
                    (name, content_hash),
                )
            else:
                # dedup: same name + same hash means same content, skip
                cur.execute(
                    "SELECT * FROM prompts WHERE name = ? AND hash = ?",
                    (name, content_hash),
                )
                row = cur.fetchone()
                if row:
                    record = self._row_to_prompt(row)
                    record.tags = self._get_tags_unlocked(cur, record.id)
                    return record

            # atomic version increment + insert in one statement
            meta_json = json.dumps(metadata) if metadata else None
            cur.execute(
                """INSERT INTO prompts (name, version, content, hash, metadata)
                   VALUES (?, (SELECT COALESCE(MAX(version), 0) + 1 FROM prompts WHERE name = ?), ?, ?, ?)""",
                (name, name, content, content_hash, meta_json),
            )
            self._conn.commit()

            # re-read the row to get the version and created_at
            cur.execute("SELECT * FROM prompts WHERE id = ?", (cur.lastrowid,))
            return self._row_to_prompt(cur.fetchone())

    def get_prompt(self, name, version=None) -> PromptRecord | None:
        with self._lock:
            cur = self._conn.cursor()
            if version is not None:
                cur.execute(
                    "SELECT * FROM prompts WHERE name = ? AND version = ?",
                    (name, version),
                )
            else:
                cur.execute(
                    "SELECT * FROM prompts WHERE name = ? ORDER BY version DESC LIMIT 1",
                    (name,),
                )
            row = cur.fetchone()
            if not row:
                return None
            record = self._row_to_prompt(row)
            record.tags = self._get_tags_unlocked(cur, record.id)
            return record

    def get_prompt_by_tag(self, name, tag) -> PromptRecord | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """SELECT p.* FROM prompts p
                   JOIN prompt_tags pt ON p.id = pt.prompt_id
                   WHERE p.name = ? AND pt.tag = ?
                   ORDER BY p.version DESC LIMIT 1""",
                (name, tag),
            )
            row = cur.fetchone()
            if not row:
                return None
            record = self._row_to_prompt(row)
            record.tags = self._get_tags_unlocked(cur, record.id)
            return record

    def list_prompts(self, name=None, offset=0, limit=100) -> list[PromptRecord]:
        with self._lock:
            cur = self._conn.cursor()
            if name:
                cur.execute(
                    """SELECT p.*, GROUP_CONCAT(pt.tag) as tags_csv
                       FROM prompts p LEFT JOIN prompt_tags pt ON p.id = pt.prompt_id
                       WHERE p.name = ? GROUP BY p.id ORDER BY p.version ASC
                       LIMIT ? OFFSET ?""",
                    (name, limit, offset),
                )
            else:
                cur.execute(
                    """SELECT p.*, GROUP_CONCAT(pt.tag) as tags_csv
                       FROM prompts p LEFT JOIN prompt_tags pt ON p.id = pt.prompt_id
                       GROUP BY p.id ORDER BY p.name, p.version ASC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                )
            records = []
            for row in cur.fetchall():
                record = self._row_to_prompt(row)
                tags_csv = row["tags_csv"]
                record.tags = tags_csv.split(",") if tags_csv else []
                records.append(record)
            return records

    def list_prompt_summaries(self, offset=0, limit=200) -> list[dict]:
        """One row per prompt NAME: its latest version + distinct tags.

        The registry needs names, not versions. Paginating list_prompts()
        by row meant a single heavily-versioned prompt (e.g. 86 versions)
        could fill the whole page and hide every other name. This
        aggregates in SQL so the limit applies to names.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT p.name AS name,
                       MAX(p.version) AS latest_version,
                       (SELECT GROUP_CONCAT(DISTINCT pt.tag)
                          FROM prompt_tags pt
                          JOIN prompts p2 ON pt.prompt_id = p2.id
                         WHERE p2.name = p.name) AS tags_csv
                  FROM prompts p
                 GROUP BY p.name
                 ORDER BY p.name
                 LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            out = []
            for row in cur.fetchall():
                tags_csv = row["tags_csv"]
                out.append({
                    "name": row["name"],
                    "latest_version": row["latest_version"],
                    "tags": sorted(set(tags_csv.split(","))) if tags_csv else [],
                })
            return out

    def list_latest_contents(self, limit: int = 500) -> list[tuple[str, str]]:
        """(name, content) for the latest version of every prompt name.

        "Latest" matches get_prompt()'s notion: MAX(version) per name. A
        self-join on the per-name max version avoids the get_prompt() N+1
        that prompt_search used to do (one query total instead of 1 + N).
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT p.name AS name, p.content AS content
                  FROM prompts p
                  JOIN (
                        SELECT name, MAX(version) AS max_version
                          FROM prompts
                         GROUP BY name
                       ) latest
                    ON p.name = latest.name AND p.version = latest.max_version
                 ORDER BY p.name
                 LIMIT ?
                """,
                (limit,),
            )
            return [(row["name"], row["content"]) for row in cur.fetchall()]

    def get_invocation_stats(self, name: str, days: int = 30) -> dict:
        """Per-call distribution for one prompt over the window: count plus
        min/avg/p50/p95/max for input tokens, output tokens, cost and
        latency, and a histogram of input-token size for a distribution
        curve. All derived from the invocations ledger."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT tokens_in, tokens_out, cost, latency_ms FROM invocations
                   WHERE prompt_name = ? AND created_at >= datetime('now', ? || ' days')""",
                (name, f"-{days}"),
            )
            rows = cur.fetchall()

        # Distribution (percentiles + histogram) needs the raw per-row values,
        # so we read the typed columns directly — no json.loads per row.
        series = {"tokens_in": [], "tokens_out": [], "cost": [], "latency_ms": []}
        for r in rows:
            if r["tokens_in"] is not None:
                series["tokens_in"].append(float(r["tokens_in"]))
            if r["tokens_out"] is not None:
                series["tokens_out"].append(float(r["tokens_out"]))
            if r["cost"] is not None:
                series["cost"].append(float(r["cost"]))
            if r["latency_ms"] is not None:
                series["latency_ms"].append(float(r["latency_ms"]))

        def _pct(sorted_vals, p):
            if not sorted_vals:
                return 0.0
            k = (len(sorted_vals) - 1) * p
            lo = int(k)
            hi = min(lo + 1, len(sorted_vals) - 1)
            return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

        def _summary(vals):
            if not vals:
                return {"min": 0, "avg": 0, "p50": 0, "p95": 0, "max": 0, "sum": 0}
            s = sorted(vals)
            return {
                "min": s[0],
                "avg": sum(s) / len(s),
                "p50": _pct(s, 0.50),
                "p95": _pct(s, 0.95),
                "max": s[-1],
                "sum": sum(s),
            }

        metrics = {k: _summary(v) for k, v in series.items()}

        # Histogram of input-token size (distribution curve), 12 even bins.
        hist = []
        ti_vals = sorted(series["tokens_in"])
        if ti_vals:
            lo, hi = ti_vals[0], ti_vals[-1]
            nbins = 12
            if hi <= lo:
                hist = [{"start": lo, "end": hi, "count": len(ti_vals)}]
            else:
                width = (hi - lo) / nbins
                counts = [0] * nbins
                for v in ti_vals:
                    idx = min(int((v - lo) / width), nbins - 1)
                    counts[idx] += 1
                hist = [
                    {"start": round(lo + i * width), "end": round(lo + (i + 1) * width), "count": c}
                    for i, c in enumerate(counts)
                ]

        return {"name": name, "days": days, "count": len(rows), "metrics": metrics, "histogram": hist}

    def set_prompt_env(self, name: str, version: int, env: str) -> bool:
        """Point an environment tag (e.g. 'prod') at a specific version,
        moving it off whatever version currently holds it. Returns False if
        the version doesn't exist."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM prompts WHERE name = ? AND version = ?", (name, version)
            )
            row = cur.fetchone()
            if not row:
                return False
            target_id = row["id"]
            # Clear this env tag from all versions of the prompt.
            self._conn.execute(
                """DELETE FROM prompt_tags WHERE tag = ? AND prompt_id IN
                   (SELECT id FROM prompts WHERE name = ?)""",
                (env, name),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag) VALUES (?, ?)",
                (target_id, env),
            )
            self._conn.commit()
            return True

    def get_runs_for_prompt(self, prompt_name: str, limit: int = 50) -> list[dict]:
        """Eval runs that exercised a given prompt, newest first, with the
        prompt version each ran against. Powers the prompt↔eval linkage so a
        prompt page can show 'did this version pass?'."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT id, suite_name, prompt_version, model_version,
                          timestamp, overall_pass, overall_score
                   FROM eval_runs WHERE prompt_name = ?
                   ORDER BY id DESC LIMIT ?""",
                (prompt_name, limit),
            )
            return [
                {
                    "run_id": r["id"],
                    "suite_name": r["suite_name"],
                    "prompt_version": r["prompt_version"],
                    "model_version": r["model_version"],
                    "timestamp": r["timestamp"],
                    "passed": bool(r["overall_pass"]),
                    "score": r["overall_score"],
                }
                for r in cur.fetchall()
            ]

    # sortable columns -> SQL expression (metadata fields live in a JSON blob)
    _INV_SORT = {
        "created_at": "id",  # id tracks insertion order == recency, no tz parsing
        # typed columns (migration 8) — indexable, no per-row JSON parsing
        "cost": "cost",
        "latency_ms": "latency_ms",
        "tokens_in": "tokens_in",
        "tokens_out": "tokens_out",
    }

    def list_invocations(self, name: str | None = None, days: int = 7, limit: int = 100,
                         offset: int = 0, captured_only: bool = False, order: str = "recent",
                         sort: str | None = None, direction: str = "desc",
                         min_rating: float | None = None) -> list[dict]:
        """Invocations for the trace/cost lists, paged via limit/offset. Sorting
        is server-side: pass sort=<column> + direction=asc|desc, or the legacy
        order='recent'|'cost'. Joins the latest feedback rating per request_id."""
        import json as _json
        clauses = ["created_at >= datetime('now', ? || ' days')"]
        params: list = [f"-{days}"]
        if name:
            clauses.append("prompt_name = ?")
            params.append(name)
        if captured_only:
            clauses.append("(input_text IS NOT NULL OR output_text IS NOT NULL)")
        if sort and sort in self._INV_SORT:
            d = "ASC" if str(direction).lower() == "asc" else "DESC"
            order_sql = f"ORDER BY {self._INV_SORT[sort]} {d}, id DESC"
        else:
            order_sql = (
                "ORDER BY cost DESC, id DESC"
                if order == "cost" else "ORDER BY id DESC"
            )
        params += [limit, offset]
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT id, prompt_name, prompt_version, metadata, created_at,
                           input_text, output_text, request_id
                    FROM invocations WHERE {' AND '.join(clauses)}
                    {order_sql} LIMIT ? OFFSET ?""",
                params,
            )
            rows = cur.fetchall()
            # Latest feedback rating/comment per request_id seen.
            req_ids = [r["request_id"] for r in rows if r["request_id"]]
            fb: dict[str, dict] = {}
            if req_ids:
                qmarks = ",".join("?" * len(req_ids))
                fcur = self._conn.execute(
                    f"""SELECT request_id, rating, comment FROM feedback
                        WHERE request_id IN ({qmarks}) ORDER BY id ASC""",
                    req_ids,
                )
                for fr in fcur.fetchall():
                    fb[fr["request_id"]] = {"rating": fr["rating"], "comment": fr["comment"]}
        out = []
        for r in rows:
            meta = _json.loads(r["metadata"]) if r["metadata"] else {}
            f = fb.get(r["request_id"]) if r["request_id"] else None
            row = {
                "id": r["id"],
                "prompt_name": r["prompt_name"],
                "prompt_version": r["prompt_version"],
                "created_at": r["created_at"],
                "model": meta.get("model"),
                "tokens_in": meta.get("tokens_in", meta.get("prompt_tokens")),
                "tokens_out": meta.get("tokens_out", meta.get("completion_tokens")),
                "cost": meta.get("cost"),
                "latency_ms": meta.get("latency_ms"),
                "has_capture": bool(r["input_text"] or r["output_text"]),
                "output_preview": (r["output_text"] or "")[:160],
                "rating": f["rating"] if f else None,
                "comment": f["comment"] if f else None,
            }
            if min_rating is None or (row["rating"] is not None and row["rating"] <= min_rating):
                out.append(row)
        return out

    def get_invocation(self, invocation_id: int) -> dict | None:
        """Full invocation incl. captured text and all feedback for the trace view."""
        import json as _json
        with self._lock:
            cur = self._conn.execute(
                """SELECT id, prompt_name, prompt_version, metadata, created_at,
                          input_text, output_text, request_id FROM invocations WHERE id = ?""",
                (invocation_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            feedback = []
            if r["request_id"]:
                fcur = self._conn.execute(
                    "SELECT rating, comment, source, created_at FROM feedback WHERE request_id = ? ORDER BY id DESC",
                    (r["request_id"],),
                )
                feedback = [dict(fr) for fr in fcur.fetchall()]
        return {
            "id": r["id"],
            "prompt_name": r["prompt_name"],
            "prompt_version": r["prompt_version"],
            "created_at": r["created_at"],
            "request_id": r["request_id"],
            "metadata": _json.loads(r["metadata"]) if r["metadata"] else {},
            "input_text": r["input_text"],
            "output_text": r["output_text"],
            "feedback": feedback,
        }

    def save_feedback(self, request_id: str, rating: float | None = None,
                      comment: str | None = None, source: str | None = None) -> int:
        """Store an end-user rating/comment, correlated to an invocation by
        request_id. Backfills prompt_name from the matching invocation."""
        with self._lock:
            prompt_name = None
            if request_id:
                cur = self._conn.execute(
                    "SELECT prompt_name FROM invocations WHERE request_id = ? ORDER BY id DESC LIMIT 1",
                    (request_id,),
                )
                row = cur.fetchone()
                if row:
                    prompt_name = row["prompt_name"]
            cur = self._conn.execute(
                "INSERT INTO feedback (request_id, prompt_name, rating, comment, source) VALUES (?, ?, ?, ?, ?)",
                (request_id, prompt_name, rating, comment, source),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_feedback(self, name: str | None = None, days: int = 30, limit: int = 50,
                      offset: int = 0, only_comments: bool = False,
                      min_rating: float | None = None, q: str | None = None) -> list[dict]:
        """End-user feedback rows for the Feedback view, each linked back to the
        invocation that produced the response (id + model) so a row can open the
        trace. Newest-first, paged via limit/offset; q searches comment + prompt
        name (LIKE); min_rating keeps only ratings <= that."""
        import json as _json
        clauses = ["f.created_at >= datetime('now', ? || ' days')"]
        params: list = [f"-{days}"]
        if name:
            clauses.append("f.prompt_name = ?")
            params.append(name)
        if only_comments:
            clauses.append("f.comment IS NOT NULL AND TRIM(f.comment) != ''")
        if min_rating is not None:
            clauses.append("f.rating IS NOT NULL AND f.rating <= ?")
            params.append(min_rating)
        if q:
            clauses.append("(f.comment LIKE ? OR f.prompt_name LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        params += [limit, offset]
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT f.id, f.request_id, f.prompt_name, f.rating, f.comment,
                           f.source, f.created_at,
                           (SELECT i.id FROM invocations i WHERE i.request_id = f.request_id
                            ORDER BY i.id DESC LIMIT 1) AS invocation_id,
                           (SELECT i.metadata FROM invocations i WHERE i.request_id = f.request_id
                            ORDER BY i.id DESC LIMIT 1) AS metadata
                    FROM feedback f
                    WHERE {' AND '.join(clauses)}
                    ORDER BY f.id DESC LIMIT ? OFFSET ?""",
                params,
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            meta = _json.loads(r["metadata"]) if r["metadata"] else {}
            out.append({
                "id": r["id"], "request_id": r["request_id"], "prompt_name": r["prompt_name"],
                "rating": r["rating"], "comment": r["comment"], "source": r["source"],
                "created_at": r["created_at"], "invocation_id": r["invocation_id"],
                "model": meta.get("model"),
            })
        return out

    def get_feedback_stats(self, days: int = 30, positive_at: float = 0.7,
                           negative_at: float = 0.4) -> dict:
        """Aggregate feedback health over the window: satisfaction (share of
        rated feedback that's positive), counts, a per-prompt breakdown, and a
        daily positive-rate sparkline."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT rating, comment, prompt_name, substr(created_at, 1, 10) AS day
                   FROM feedback WHERE created_at >= datetime('now', ? || ' days')""",
                (f"-{days}",),
            )
            rows = cur.fetchall()
        total = len(rows)
        rated = [r["rating"] for r in rows if r["rating"] is not None]
        positive = sum(1 for v in rated if v >= positive_at)
        negative = sum(1 for v in rated if v <= negative_at)
        with_comments = sum(1 for r in rows if r["comment"] and r["comment"].strip())
        by_prompt: dict[str, dict] = {}
        for r in rows:
            if r["rating"] is None:
                continue
            e = by_prompt.setdefault(r["prompt_name"] or "(unknown)", {"name": r["prompt_name"] or "(unknown)", "count": 0, "sum": 0.0, "neg": 0})
            e["count"] += 1
            e["sum"] += r["rating"]
            if r["rating"] <= negative_at:
                e["neg"] += 1
        by_day: dict[str, list] = {}
        for r in rows:
            if r["rating"] is None:
                continue
            by_day.setdefault(r["day"], []).append(r["rating"])
        spark = [
            round(sum(1 for v in vs if v >= positive_at) / len(vs), 3)
            for _, vs in sorted(by_day.items())
        ]
        return {
            "days": days,
            "total": total,
            "rated": len(rated),
            "positive": positive,
            "negative": negative,
            "neutral": len(rated) - positive - negative,
            "with_comments": with_comments,
            "positive_rate": round(positive / len(rated), 4) if rated else None,
            "avg_rating": round(sum(rated) / len(rated), 4) if rated else None,
            # all prompts (worst first) so the UI can search the full catalog
            "by_prompt": sorted(
                ({"name": e["name"], "count": e["count"],
                  "avg_rating": round(e["sum"] / e["count"], 3),
                  "negative": e["neg"]} for e in by_prompt.values()),
                key=lambda x: (-x["negative"], -x["count"]),
            ),
            "sparkline": spark[-14:],
        }

    def bisect_regression(self, suite_name: str) -> dict:
        """Find the first eval run where the suite went from passing to
        failing (a `git bisect` for evals), with the prompt/model deltas at
        that boundary. Returns {found, run, previous} or {found: False}."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT id, prompt_version, model_version, timestamp,
                          overall_pass, overall_score
                   FROM eval_runs WHERE suite_name = ? ORDER BY id ASC""",
                (suite_name,),
            )
            runs = cur.fetchall()
        prev = None
        for r in runs:
            if prev is not None and prev["overall_pass"] and not r["overall_pass"]:
                return {
                    "found": True,
                    "suite": suite_name,
                    "first_bad": {
                        "run_id": r["id"], "prompt_version": r["prompt_version"],
                        "model_version": r["model_version"], "timestamp": r["timestamp"],
                        "score": r["overall_score"],
                    },
                    "last_good": {
                        "run_id": prev["id"], "prompt_version": prev["prompt_version"],
                        "model_version": prev["model_version"], "timestamp": prev["timestamp"],
                        "score": prev["overall_score"],
                    },
                    "prompt_changed": prev["prompt_version"] != r["prompt_version"],
                    "model_changed": prev["model_version"] != r["model_version"],
                }
            prev = r
        return {"found": False, "suite": suite_name,
                "reason": "No passing→failing transition found." if runs else "No runs."}

    # ---- budgets ----

    def save_budget(self, scope: str, period: str, limit_usd: float, target: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO budgets (scope, target, period, limit_usd) VALUES (?, ?, ?, ?)",
                (scope, target, period, limit_usd),
            )
            self._conn.commit()
            return cur.lastrowid

    def delete_budget(self, budget_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
            self._conn.commit()

    def list_budgets(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM budgets ORDER BY id")
            return [dict(r) for r in cur.fetchall()]

    def get_budget_status(self) -> list[dict]:
        """Each budget with its current-period spend, % used, and breach flag.
        Spend is summed from the invocations ledger over the period window."""
        budgets = self.list_budgets()
        if not budgets:
            return []

        import datetime as _dt
        now = _dt.datetime.utcnow()
        day_start = now.strftime("%Y-%m-%d")
        month_start = now.strftime("%Y-%m")

        # One pass over the ledger: per-prompt daily + monthly spend from the
        # typed cost column. Budgets are then satisfied from this small
        # per-prompt table in Python (no per-budget re-scan, no json.loads).
        with self._lock:
            cur = self._conn.execute(
                """SELECT prompt_name AS name,
                          COALESCE(SUM(CASE WHEN created_at LIKE ? || '%'
                                            THEN COALESCE(cost, 0) ELSE 0 END), 0) AS day_spend,
                          COALESCE(SUM(CASE WHEN created_at LIKE ? || '%'
                                            THEN COALESCE(cost, 0) ELSE 0 END), 0) AS month_spend
                   FROM invocations
                   WHERE created_at >= datetime('now', '-31 days')
                   GROUP BY prompt_name""",
                (day_start, month_start),
            )
            per_prompt = [(r["name"], float(r["day_spend"]), float(r["month_spend"])) for r in cur.fetchall()]

        def matches(name: str, scope: str, target: str | None) -> bool:
            if scope == "global":
                return True
            mod = name.split(".")[0] if "." in name else name
            return (scope == "module" and mod == target) or (scope == "prompt" and name == target)

        out = []
        for b in budgets:
            spend = 0.0
            for name, day_spend, month_spend in per_prompt:
                if matches(name, b["scope"], b["target"]):
                    spend += day_spend if b["period"] == "daily" else month_spend
            limit = b["limit_usd"] or 0
            pct = (spend / limit * 100) if limit > 0 else 0
            out.append({
                **b,
                "spend": round(spend, 6),
                "pct": round(pct, 1),
                "breached": spend > limit and limit > 0,
            })
        return out

    def get_invocation_models(self, days: int = 30) -> list[dict]:
        """Distinct models seen in the invocations ledger over the window,
        with call counts. Used to flag models that have no pricing entry."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT model, COUNT(*) AS calls FROM invocations
                   WHERE created_at >= datetime('now', ? || ' days')
                     AND model IS NOT NULL AND model != ''
                   GROUP BY model
                   ORDER BY calls DESC, MIN(id) ASC""",
                (f"-{days}",),
            )
            return [{"model": r["model"], "calls": int(r["calls"])} for r in cur.fetchall()]

    def get_model_cost_summary(self, model: str, days: int = 30) -> dict:
        """SQL-side cost/latency rollup for a single model over the window.
        Returns {cost, calls, avg_latency}. Used by model_compare instead of
        scanning the whole ledger twice with days=3650."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT COALESCE(SUM(cost), 0.0) AS cost,
                          COUNT(*) AS calls,
                          AVG(latency_ms) AS avg_latency
                   FROM invocations
                   WHERE model = ? AND created_at >= datetime('now', ? || ' days')""",
                (model, f"-{days}"),
            )
            r = cur.fetchone()
        return {
            "cost": float(r["cost"] or 0.0),
            "calls": int(r["calls"] or 0),
            "avg_latency": float(r["avg_latency"]) if r["avg_latency"] is not None else 0.0,
        }

    def prune_prompt_versions(self, name: str, keep_last: int = 1) -> int:
        """Delete all but the newest *keep_last* versions of a prompt.

        For collapsing legacy baked-prompt churn (hundreds of versions that
        differ only by interpolated data) down to a representative few.
        Returns the number of versions deleted."""
        keep_last = max(1, int(keep_last))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT version FROM prompts WHERE name = ? ORDER BY version DESC LIMIT ?",
                (name, keep_last),
            )
            kept = [r["version"] for r in cur.fetchall()]
            if not kept:
                return 0
            cutoff = min(kept)
            cur.execute(
                "DELETE FROM prompts WHERE name = ? AND version < ?", (name, cutoff)
            )
            deleted = cur.rowcount
            # Drop tags orphaned by the delete.
            cur.execute(
                "DELETE FROM prompt_tags WHERE prompt_id NOT IN (SELECT id FROM prompts)"
            )
            self._conn.commit()
            return deleted

    def tag_prompt(self, prompt_id, tag):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag) VALUES (?, ?)",
                (prompt_id, tag),
            )
            self._conn.commit()

    def get_tags(self, prompt_id) -> list[str]:
        with self._lock:
            return self._get_tags_unlocked(self._conn.cursor(), prompt_id)

    def _get_tags_unlocked(self, cur, prompt_id) -> list[str]:
        """Get tags without acquiring the lock (caller must hold it)."""
        cur.execute(
            "SELECT tag FROM prompt_tags WHERE prompt_id = ?",
            (prompt_id,),
        )
        return [row[0] for row in cur.fetchall()]

    # ---- eval runs ----

    def save_eval_run(
        self,
        suite_name,
        prompt_name=None,
        prompt_version=None,
        model_version=None,
        overall_pass=True,
        overall_score=None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO eval_runs
                   (suite_name, prompt_name, prompt_version, model_version, overall_pass, overall_score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (suite_name, prompt_name, prompt_version, model_version, int(overall_pass), overall_score),
            )
            self._conn.commit()
            return cur.lastrowid

    def save_eval_result(
        self,
        run_id,
        test_name,
        assertion_type,
        passed,
        score=None,
        details=None,
        latency_ms=None,
    ) -> int:
        with self._lock:
            details_json = json.dumps(details) if details else None
            cur = self._conn.execute(
                """INSERT INTO eval_results
                   (run_id, test_name, assertion_type, passed, score, details, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, test_name, assertion_type, int(passed), score, details_json, latency_ms),
            )
            self._conn.commit()
            return cur.lastrowid

    def save_eval_run_atomic(
        self,
        *,
        results: list[dict],
        suite_name,
        prompt_name=None,
        prompt_version=None,
        model_version=None,
        overall_pass=True,
        overall_score=None,
    ) -> int:
        """Insert the run row and every result row inside a single
        transaction (one commit), so a crash or write failure can never leave
        a visible run whose per-assertion results are partial or missing."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    """INSERT INTO eval_runs
                       (suite_name, prompt_name, prompt_version, model_version, overall_pass, overall_score)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (suite_name, prompt_name, prompt_version, model_version,
                     int(overall_pass), overall_score),
                )
                run_id = cur.lastrowid
                for r in results:
                    details_json = json.dumps(r["details"]) if r.get("details") else None
                    self._conn.execute(
                        """INSERT INTO eval_results
                           (run_id, test_name, assertion_type, passed, score, details, latency_ms)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, r["test_name"], r["assertion_type"], int(r["passed"]),
                         r.get("score"), details_json, r.get("latency_ms")),
                    )
                self._conn.commit()
                return run_id
            except Exception:
                self._conn.rollback()
                raise

    def get_eval_runs(self, suite_name, offset=0, limit=50) -> list[EvalRunRecord]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM eval_runs
                   WHERE suite_name = ?
                   ORDER BY id DESC LIMIT ? OFFSET ?""",
                (suite_name, limit, offset),
            )
            return [self._row_to_eval_run(row) for row in cur.fetchall()]

    def get_eval_runs_batch(self, suite_names: list[str], limit_per_suite: int = 20) -> dict[str, list[EvalRunRecord]]:
        if not suite_names:
            return {}
        with self._lock:
            placeholders = ",".join("?" for _ in suite_names)
            cur = self._conn.execute(
                f"""SELECT * FROM (
                        SELECT *, ROW_NUMBER() OVER (
                            PARTITION BY suite_name ORDER BY id DESC
                        ) AS rn
                        FROM eval_runs
                        WHERE suite_name IN ({placeholders})
                    ) WHERE rn <= ?""",
                list(suite_names) + [limit_per_suite],
            )
            result: dict[str, list[EvalRunRecord]] = {name: [] for name in suite_names}
            for row in cur.fetchall():
                record = self._row_to_eval_run(row)
                result[record.suite_name].append(record)
            return result

    def get_eval_results(self, run_id) -> list[EvalResultRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM eval_results WHERE run_id = ?",
                (run_id,),
            )
            return [self._row_to_eval_result(row) for row in cur.fetchall()]

    def get_eval_results_batch(self, run_ids: list[int]) -> dict[int, list[EvalResultRecord]]:
        if not run_ids:
            return {}
        with self._lock:
            placeholders = ",".join("?" for _ in run_ids)
            cur = self._conn.execute(
                f"SELECT * FROM eval_results WHERE run_id IN ({placeholders})",
                list(run_ids),
            )
            result: dict[int, list[EvalResultRecord]] = {rid: [] for rid in run_ids}
            for row in cur.fetchall():
                record = self._row_to_eval_result(row)
                result[record.run_id].append(record)
            return result

    def get_runs_by_model(self, suite_name, model_version, limit=200) -> list[EvalRunRecord]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM eval_runs
                   WHERE suite_name = ? AND model_version = ?
                   ORDER BY id DESC LIMIT ?""",
                (suite_name, model_version, limit),
            )
            return [self._row_to_eval_run(row) for row in cur.fetchall()]

    def get_model_versions(self, suite_name) -> list[tuple[str, int]]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT model_version, COUNT(*) as cnt FROM eval_runs
                   WHERE suite_name = ? AND model_version IS NOT NULL
                   GROUP BY model_version ORDER BY cnt DESC""",
                (suite_name,),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]

    def get_score_history(self, suite_name, limit=30) -> list[tuple[str, float]]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT timestamp, overall_score FROM eval_runs
                   WHERE suite_name = ? AND overall_score IS NOT NULL
                   ORDER BY id DESC LIMIT ?""",
                (suite_name, limit),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]

    def list_suite_names(self) -> list[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT suite_name FROM eval_runs ORDER BY suite_name"
            )
            return [row[0] for row in cur.fetchall()]

    def count_invocations(self) -> int:
        """Total rows in the per-call ledger (all time). A cheap existence
        check for onboarding — no window, no joins."""
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM invocations")
            return int(cur.fetchone()[0])

    def get_eval_run_by_id(self, run_id: int) -> EvalRunRecord | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM eval_runs WHERE id = ?",
                (run_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_eval_run(row)

    def get_cost_data(self, days: int = 7, name: str | None = None, model: str | None = None) -> dict:
        from promptry.pricing import (
            REROUTES,
            cache_savings as _cache_savings,
            calculate_cost as _calc_cost,
            ensure_prices_loaded as _ensure_prices_loaded,
            resolve_model as _resolve_model,
        )
        # REROUTES is read directly below (the `if REROUTES:` prefilter), not
        # through calculate_cost/resolve_model, so it must trigger the lazy
        # persisted-price load itself or a refreshed reroute table would be
        # silently ignored on this path.
        _ensure_prices_loaded()

        where = ["created_at >= datetime('now', ? || ' days')"]
        params: list = [f"-{days}"]
        if name is not None:
            where.append("prompt_name = ?")
            params.append(name)
        if model is not None:
            # Original matched meta.get("model", "") == model, so a row with
            # no model never matches a model filter.
            where.append("COALESCE(model, '') = ?")
            params.append(model)
        where_sql = " AND ".join(where)

        # cached_tokens / cache_write_tokens were not promoted to columns, so
        # they are still json_extract'd — but SQL-side (in C), never a Python
        # json.loads per row.
        # json_extract raises on malformed metadata (e.g. a legacy row whose
        # blob isn't valid JSON), so gate on json_valid — invalid blobs read as
        # 0 here, matching how the old Python reader swallowed json.loads errors.
        cached_expr = "CAST(COALESCE(CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.cached_tokens') END, 0) AS INTEGER)"
        cwrite_expr = "CAST(COALESCE(CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.cache_write_tokens') END, 0) AS INTEGER)"

        # Cost lives in the invocations ledger — one row per LLM call. (The
        # prompts table holds versioned templates, not per-call telemetry, so
        # it must not contribute to cost or calls would be double-counted.)
        with self._lock:
            by_name_rows = self._conn.execute(
                f"""SELECT prompt_name AS name,
                           COUNT(*) AS calls,
                           COALESCE(SUM(tokens_in), 0) AS tokens_in,
                           COALESCE(SUM(tokens_out), 0) AS tokens_out,
                           COALESCE(SUM({cached_expr}), 0) AS cached_tokens,
                           COALESCE(SUM({cwrite_expr}), 0) AS cache_write_tokens,
                           COALESCE(SUM(cost), 0.0) AS cost,
                           GROUP_CONCAT(DISTINCT model) AS models
                    FROM invocations WHERE {where_sql}
                    GROUP BY prompt_name""",
                params,
            ).fetchall()
            by_date_rows = self._conn.execute(
                f"""SELECT substr(created_at, 1, 10) AS date,
                           COUNT(*) AS calls,
                           COALESCE(SUM(tokens_in), 0) AS tokens_in,
                           COALESCE(SUM(tokens_out), 0) AS tokens_out,
                           COALESCE(SUM({cached_expr}), 0) AS cached_tokens,
                           COALESCE(SUM(cost), 0.0) AS cost
                    FROM invocations
                    WHERE {where_sql} AND created_at IS NOT NULL AND created_at != ''
                    GROUP BY substr(created_at, 1, 10)
                    ORDER BY date ASC""",
                params,
            ).fetchall()
            # Cached tokens grouped per (prompt, model) so cache-savings pricing
            # is applied per model, not per row.
            savings_rows = self._conn.execute(
                f"""SELECT prompt_name AS name, model,
                           COALESCE(SUM({cached_expr}), 0) AS cached_tokens
                    FROM invocations
                    WHERE {where_sql} AND model IS NOT NULL AND model != ''
                    GROUP BY prompt_name, model""",
                params,
            ).fetchall()
            # Per-row detail for rerouted-slug rows only, fetched inside the
            # same lock/window so the reroute adjustment below stays consistent
            # with the aggregates above. Skipped entirely when no reroutes are
            # configured (the common case) so non-xAI deployments pay nothing.
            reroute_rows: list = []
            if REROUTES:
                # resolve_model also fuzzy-matches dated variants of a
                # REROUTES key by prefix (e.g. "grok-4-1-fast-non-reasoning"
                # matches "grok-4-1-fast-non-reasoning-1234"), so the
                # candidate prefilter must be a prefix LIKE, not an exact IN
                # — otherwise those rows are silently skipped. REROUTES keys
                # contain no '_' or '%' today, but escape defensively since
                # both are LIKE wildcards.
                reroute_keys = sorted(REROUTES)
                like_clause = " OR ".join(
                    ["model LIKE ? || '%' ESCAPE '\\'"] * len(reroute_keys)
                )
                escaped_keys = [
                    k.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    for k in reroute_keys
                ]
                reroute_rows = self._conn.execute(
                    f"""SELECT prompt_name AS name, model, tokens_in, tokens_out,
                               created_at, cost,
                               CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.cached_tokens') END AS cached_tokens,
                               CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.cache_write_tokens') END AS cache_write_tokens
                        FROM invocations
                        WHERE {where_sql} AND ({like_clause})""",
                    params + escaped_keys,
                ).fetchall()

        # cache savings per (name, model) group -> per-name totals.
        savings_by_name: dict[str, float] = {}
        for sr in savings_rows:
            cached = int(sr["cached_tokens"] or 0)
            if not cached:
                continue
            s = _cache_savings(sr["model"], cached)
            if s:
                savings_by_name[sr["name"]] = savings_by_name.get(sr["name"], 0.0) + s
        total_savings = sum(savings_by_name.values())

        by_name = []
        for r in by_name_rows:
            ti = int(r["tokens_in"])
            cached = int(r["cached_tokens"])
            models_csv = r["models"]
            models = sorted({m for m in (models_csv.split(",") if models_csv else []) if m})
            by_name.append({
                "name": r["name"],
                "calls": int(r["calls"]),
                "tokens_in": ti,
                "tokens_out": int(r["tokens_out"]),
                "cached_tokens": cached,
                "cache_write_tokens": int(r["cache_write_tokens"]),
                "cost": float(r["cost"]),
                "cache_savings": round(savings_by_name.get(r["name"], 0.0), 6),
                "cache_hit_rate": (cached / ti if ti > 0 else 0.0),
                "models": models,
            })
        by_name.sort(key=lambda x: x["cost"], reverse=True)

        by_date = [
            {
                "date": r["date"],
                "calls": int(r["calls"]),
                "tokens_in": int(r["tokens_in"]),
                "tokens_out": int(r["tokens_out"]),
                "cached_tokens": int(r["cached_tokens"]),
                "cost": float(r["cost"]),
            }
            for r in by_date_rows
        ]

        # Honor provider reroutes: a retired slug (e.g.
        # grok-4-1-fast-non-reasoning on/after 2026-05-15) bills at its
        # replacement's rate. The SQL aggregates above trust the stored cost;
        # for rerouted-slug rows we recompute from tokens so the dashboard is
        # correct even for rows whose stored cost predates the reroute — the
        # served model (if logged) already prices right and isn't in REROUTES,
        # so it's unaffected. Applied as a per-row delta against the stored cost
        # (COALESCE 0) so only rerouted rows move; everything else keeps the
        # SQL-summed value byte-for-byte.
        if reroute_rows:
            by_name_by_key = {e["name"]: e for e in by_name}
            by_date_by_key = {e["date"]: e for e in by_date}
            for rr in reroute_rows:
                row_model = rr["model"] or ""
                date_str = rr["created_at"][:10] if rr["created_at"] else ""
                if _resolve_model(row_model, date_str) == row_model:
                    continue
                rc = _calc_cost(
                    row_model,
                    tokens_in=int(rr["tokens_in"] or 0),
                    tokens_out=int(rr["tokens_out"] or 0),
                    cached_tokens=int(rr["cached_tokens"] or 0),
                    cache_write_tokens=int(rr["cache_write_tokens"] or 0),
                    when=date_str,
                )
                if rc is None:
                    continue
                delta = rc - float(rr["cost"] or 0.0)
                ne = by_name_by_key.get(rr["name"])
                if ne is not None:
                    ne["cost"] += delta
                de = by_date_by_key.get(date_str)
                if de is not None:
                    de["cost"] += delta
            by_name.sort(key=lambda x: x["cost"], reverse=True)

        total_cost = sum(e["cost"] for e in by_name)
        total_calls = sum(e["calls"] for e in by_name)
        total_tokens_in = sum(e["tokens_in"] for e in by_name)
        total_tokens_out = sum(e["tokens_out"] for e in by_name)
        total_cached_tokens = sum(e["cached_tokens"] for e in by_name)
        total_cache_write_tokens = sum(e["cache_write_tokens"] for e in by_name)

        avg_cost = (total_cost / total_calls) if total_calls > 0 else 0.0
        overall_hit_rate = (
            total_cached_tokens / total_tokens_in if total_tokens_in > 0 else 0.0
        )

        return {
            "summary": {
                "total_cost": total_cost,
                "total_calls": total_calls,
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "total_cached_tokens": total_cached_tokens,
                "total_cache_write_tokens": total_cache_write_tokens,
                "cache_hit_rate": overall_hit_rate,
                "cache_savings": round(total_savings, 6),
                "uncached_cost": round(total_cost + total_savings, 6),
                "avg_cost": avg_cost,
            },
            "by_name": by_name,
            "by_date": by_date,
        }

    # ---- datasets ----

    def save_dataset(self, name: str, items: list, metadata=None) -> int:
        """Save a dataset of input/output pairs. Returns version number."""
        with self._lock:
            cur = self._conn.cursor()
            items_json = json.dumps(items)
            meta_json = json.dumps(metadata) if metadata else None
            cur.execute(
                """INSERT INTO datasets (name, version, items, metadata)
                   VALUES (?, (SELECT COALESCE(MAX(version), 0) + 1 FROM datasets WHERE name = ?), ?, ?)""",
                (name, name, items_json, meta_json),
            )
            self._conn.commit()
            cur.execute("SELECT version FROM datasets WHERE id = ?", (cur.lastrowid,))
            return cur.fetchone()[0]

    def get_dataset(self, name: str, version: int | None = None) -> dict | None:
        """Get a dataset by name. Returns latest version if no version given."""
        with self._lock:
            cur = self._conn.cursor()
            if version is not None:
                cur.execute(
                    "SELECT * FROM datasets WHERE name = ? AND version = ?",
                    (name, version),
                )
            else:
                cur.execute(
                    "SELECT * FROM datasets WHERE name = ? ORDER BY version DESC LIMIT 1",
                    (name,),
                )
            row = cur.fetchone()
            if not row:
                return None
            try:
                meta = json.loads(row["metadata"]) if row["metadata"] else None
            except (json.JSONDecodeError, TypeError):
                meta = None
            return {
                "id": row["id"],
                "name": row["name"],
                "version": row["version"],
                "items": json.loads(row["items"]),
                "metadata": meta,
                "created_at": row["created_at"],
            }

    def list_datasets(self) -> list[dict]:
        """List all datasets with name, latest version, and item count."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT d.name, d.version as latest_version, d.items
                   FROM datasets d
                   INNER JOIN (SELECT name, MAX(version) as mv FROM datasets GROUP BY name) m
                   ON d.name = m.name AND d.version = m.mv
                   ORDER BY d.name"""
            )
            results = []
            for row in cur.fetchall():
                try:
                    items = json.loads(row["items"])
                    item_count = len(items)
                except (json.JSONDecodeError, TypeError):
                    item_count = 0
                results.append({
                    "name": row["name"],
                    "latest_version": row["latest_version"],
                    "item_count": item_count,
                })
            return results

    # ---- golden examples (eval-from-trace) ----

    def add_golden_example(self, prompt_name: str, input_text: str,
                           reference_output: str | None = None,
                           source_invocation_id: int | None = None,
                           model: str | None = None) -> int:
        """Promote a trace into a per-prompt golden example. Returns its id."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO golden_examples
                   (prompt_name, input_text, reference_output, source_invocation_id, model)
                   VALUES (?, ?, ?, ?, ?)""",
                (prompt_name, input_text, reference_output, source_invocation_id, model),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_golden_examples(self, prompt_name: str) -> list[dict]:
        """Golden examples for a prompt, newest first."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT id, prompt_name, input_text, reference_output,
                          source_invocation_id, model, created_at
                   FROM golden_examples WHERE prompt_name = ? ORDER BY id DESC""",
                (prompt_name,),
            )
            return [dict(r) for r in cur.fetchall()]

    def delete_golden_example(self, example_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM golden_examples WHERE id = ?", (example_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ---- invocations ----

    def record_invocation(self, prompt_name: str, metadata: dict | None = None, prompt_version: int | None = None,
                          input_text: str | None = None, output_text: str | None = None,
                          request_id: str | None = None, response_id: str | None = None) -> int:
        """Append one row to the invocations ledger.

        Unlike save_prompt(), there is no dedup by content/hash — every
        call lands as its own row. This is the right shape for cost and
        latency dashboards where each LLM invocation is a discrete event.

        The one exception is response_id (the provider's call id): when set, a
        UNIQUE index + INSERT OR IGNORE dedups the same call seen by two capture
        layers. Returns 0 for such an ignored duplicate.

        input_text/output_text are optional captured request/response text
        (the caller decides whether/how often to capture and redacts first).
        """
        with self._lock:
            meta_json = json.dumps(metadata) if metadata else None
            cost, tokens_in, tokens_out, model, latency_ms = _metric_columns_from_metadata(metadata)
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO invocations
                   (prompt_name, prompt_version, metadata, input_text, output_text, request_id,
                    response_id, cost, tokens_in, tokens_out, model, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (prompt_name, prompt_version, meta_json, input_text, output_text, request_id,
                 response_id, cost, tokens_in, tokens_out, model, latency_ms),
            )
            self._conn.commit()
            return cur.lastrowid if cur.rowcount else 0

    # ---- votes ----

    def save_vote(self, prompt_name, response, score, prompt_version=None, message=None, metadata=None) -> int:
        """Save a vote. Returns vote id."""
        with self._lock:
            meta_json = json.dumps(metadata) if metadata else None
            cur = self._conn.execute(
                """INSERT INTO votes (prompt_name, prompt_version, response, score, message, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (prompt_name, prompt_version, response, score, message, meta_json),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_votes(self, prompt_name=None, days=30, offset=0, limit=200) -> list[dict]:
        """Get recent votes. Returns list of vote dicts."""
        with self._lock:
            params: list = [f"-{days}"]
            name_filter = ""
            if prompt_name is not None:
                name_filter = " AND prompt_name = ?"
                params.append(prompt_name)
            cur = self._conn.execute(
                f"""SELECT * FROM votes
                    WHERE created_at >= datetime('now', ? || ' days')
                    {name_filter}
                    ORDER BY id DESC LIMIT ? OFFSET ?""",
                params + [limit, offset],
            )
            rows = cur.fetchall()
            result = []
            for row in rows:
                try:
                    meta = json.loads(row["metadata"]) if row["metadata"] else None
                except (json.JSONDecodeError, TypeError):
                    meta = None
                result.append({
                    "id": row["id"],
                    "prompt_name": row["prompt_name"],
                    "prompt_version": row["prompt_version"],
                    "response": row["response"],
                    "score": row["score"],
                    "message": row["message"],
                    "metadata": meta,
                    "created_at": row["created_at"],
                })
            return result

    def get_vote_stats(self, prompt_name=None, days=30) -> dict:
        """Aggregate vote stats per prompt name and version."""
        with self._lock:
            params: list = [f"-{days}"]
            name_filter = ""
            if prompt_name is not None:
                name_filter = " AND prompt_name = ?"
                params.append(prompt_name)

            # per prompt name
            cur = self._conn.execute(
                f"""SELECT prompt_name,
                           COUNT(*) as total,
                           SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as upvotes,
                           SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) as downvotes
                    FROM votes
                    WHERE created_at >= datetime('now', ? || ' days')
                    {name_filter}
                    GROUP BY prompt_name
                    ORDER BY total DESC""",
                params,
            )
            prompt_rows = cur.fetchall()

            # per prompt name + version
            cur2 = self._conn.execute(
                f"""SELECT prompt_name, prompt_version,
                           COUNT(*) as total,
                           SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as upvotes,
                           SUM(CASE WHEN score = -1 THEN 1 ELSE 0 END) as downvotes
                    FROM votes
                    WHERE created_at >= datetime('now', ? || ' days')
                    {name_filter}
                    GROUP BY prompt_name, prompt_version
                    ORDER BY prompt_name, prompt_version""",
                params,
            )
            version_rows = cur2.fetchall()

        # build version lookup: prompt_name -> list of version dicts
        version_map: dict[str, list[dict]] = {}
        for vr in version_rows:
            vr_total = vr["total"]
            vr_up = vr["upvotes"]
            vr_down = vr["downvotes"]
            entry = {
                "version": vr["prompt_version"],
                "total": vr_total,
                "upvotes": vr_up,
                "downvotes": vr_down,
                "upvote_rate": vr_up / vr_total if vr_total > 0 else 0.0,
            }
            version_map.setdefault(vr["prompt_name"], []).append(entry)

        total_votes = 0
        total_upvotes = 0
        prompts = []
        for pr in prompt_rows:
            pr_total = pr["total"]
            pr_up = pr["upvotes"]
            pr_down = pr["downvotes"]
            total_votes += pr_total
            total_upvotes += pr_up
            prompts.append({
                "name": pr["prompt_name"],
                "total": pr_total,
                "upvotes": pr_up,
                "downvotes": pr_down,
                "upvote_rate": pr_up / pr_total if pr_total > 0 else 0.0,
                "versions": version_map.get(pr["prompt_name"], []),
            })

        return {
            "prompts": prompts,
            "total_votes": total_votes,
            "overall_upvote_rate": total_upvotes / total_votes if total_votes > 0 else 0.0,
        }

    # ---- row converters ----

    def _row_to_prompt(self, row) -> PromptRecord:
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        return PromptRecord(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            content=row["content"],
            hash=row["hash"],
            metadata=meta,
            created_at=row["created_at"],
        )

    def _row_to_eval_run(self, row) -> EvalRunRecord:
        return EvalRunRecord(
            id=row["id"],
            suite_name=row["suite_name"],
            prompt_name=row["prompt_name"],
            prompt_version=row["prompt_version"],
            model_version=row["model_version"],
            timestamp=row["timestamp"],
            overall_pass=bool(row["overall_pass"]),
            overall_score=row["overall_score"],
        )

    def _row_to_eval_result(self, row) -> EvalResultRecord:
        details = json.loads(row["details"]) if row["details"] else None
        return EvalResultRecord(
            id=row["id"],
            run_id=row["run_id"],
            test_name=row["test_name"],
            assertion_type=row["assertion_type"],
            passed=bool(row["passed"]),
            score=row["score"],
            details=details,
            latency_ms=row["latency_ms"],
        )

    # ---- users / multi-user identity ----

    def count_users(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, email, *, password_hash=None, name=None,
                    role="viewer", is_active=True) -> dict:
        email = (email or "").strip().lower()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users (email, name, password_hash, role, is_active) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, name, password_hash, role, 1 if is_active else 0),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_user_by_email(self, email) -> dict | None:
        email = (email or "").strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, email, name, role, is_active, created_at, last_login_at "
                "FROM users ORDER BY id"
            )
            return [dict(r) for r in cur.fetchall()]

    def update_user(self, user_id, *, role=None, is_active=None,
                    name=None, password_hash=None) -> bool:
        sets, params = [], []
        if role is not None:
            sets.append("role = ?")
            params.append(role)
        if is_active is not None:
            sets.append("is_active = ?")
            params.append(1 if is_active else 0)
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if password_hash is not None:
            sets.append("password_hash = ?")
            params.append(password_hash)
        if not sets:
            return False
        params.append(user_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
            )
            self._conn.commit()
            return cur.rowcount > 0

    def touch_user_login(self, user_id) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
                (user_id,),
            )
            self._conn.commit()

    def delete_user(self, user_id) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def link_identity(self, user_id, provider, subject) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO user_identities (user_id, provider, subject) "
                "VALUES (?, ?, ?)",
                (user_id, provider, subject),
            )
            self._conn.commit()

    def get_user_by_identity(self, provider, subject) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT u.* FROM users u "
                "JOIN user_identities i ON i.user_id = u.id "
                "WHERE i.provider = ? AND i.subject = ?",
                (provider, subject),
            ).fetchone()
            return dict(row) if row else None

    # ---- audit log (append-only) ----

    def record_audit(self, action, *, actor=None, actor_id=None, target=None,
                     ip=None, result="ok", detail=None) -> int:
        detail_json = json.dumps(detail, default=str) if detail is not None else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO audit_log (actor, actor_id, action, target, ip, result, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (actor, actor_id, action, target, ip, result, detail_json),
            )
            self._conn.commit()
            return cur.lastrowid

    def _audit_filter(self, action, actor, since):
        clauses, params = [], []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def list_audit(self, *, limit=100, offset=0, action=None, actor=None, since=None) -> list[dict]:
        where, params = self._audit_filter(action, actor, since)
        params = [*params, int(limit), int(offset)]
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if r.get("detail"):
                try:
                    r["detail"] = json.loads(r["detail"])
                except (ValueError, TypeError):
                    pass
        return rows

    def count_audit(self, *, action=None, actor=None, since=None) -> int:
        where, params = self._audit_filter(action, actor, since)
        with self._lock:
            return int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM audit_log{where}", params
                ).fetchone()[0]
            )

    # ---- data retention ----

    def redact_old_capture_text(self, days: int) -> int:
        cutoff = f"-{int(days)} days"
        with self._lock:
            cur = self._conn.execute(
                "UPDATE invocations SET input_text = NULL, output_text = NULL "
                "WHERE created_at < datetime('now', ?) "
                "AND (input_text IS NOT NULL OR output_text IS NOT NULL)",
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount

    def purge_old_invocations(self, days: int) -> int:
        cutoff = f"-{int(days)} days"
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM invocations WHERE created_at < datetime('now', ?)",
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount

    def purge_old_audit(self, days: int) -> int:
        cutoff = f"-{int(days)} days"
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM audit_log WHERE ts < datetime('now', ?)",
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount
