"""Postgres scale-tier backend.

Two layers:
* TestTranslate — pure unit tests of the SQLite->Postgres dialect translator.
  These run in normal CI (no Postgres needed) and guard the risky logic.
* TestLiveBackend — a smoke against a real server, only when
  PROMPTRY_POSTGRES_DSN is set. (The full conformance + storage/registry
  behavior suites also run against Postgres when that env var is set.)
"""
import os

import pytest

from promptry.storage import postgres as pg


class TestTranslate:
    def test_placeholders(self):
        s, _ = pg._translate("SELECT * FROM t WHERE a = ? AND b = ?")
        assert s == "SELECT * FROM t WHERE a = %s AND b = %s"

    def test_insert_or_ignore_and_returning(self):
        s, ret = pg._translate("INSERT OR IGNORE INTO t (a) VALUES (?)")
        assert "ON CONFLICT DO NOTHING" in s
        assert s.strip().endswith("RETURNING id")
        assert ret is True

    def test_plain_insert_gets_returning_id(self):
        s, ret = pg._translate("INSERT INTO t (a) VALUES (?)")
        assert s.strip().endswith("RETURNING id") and ret is True

    def test_schema_version_insert_no_returning(self):
        s, ret = pg._translate("INSERT INTO schema_version (version) VALUES (?)")
        assert "RETURNING" not in s and ret is False

    def test_json_numeric_vs_text(self):
        num, _ = pg._translate("SELECT json_extract(metadata, '$.cached_tokens')")
        assert "::jsonb ->> 'cached_tokens')::numeric" in num
        txt, _ = pg._translate("SELECT json_extract(metadata, '$.trace_id')")
        assert "->> 'trace_id')" in txt and "::numeric" not in txt

    def test_json_valid(self):
        s, _ = pg._translate("WHERE json_valid(metadata)")
        assert "(metadata IS JSON)" in s

    def test_datetime_interval(self):
        s, _ = pg._translate("WHERE created_at >= datetime('now', ? || ' days')")
        assert "to_char(now() + (%s || ' days')::interval" in s

    def test_group_concat(self):
        s, _ = pg._translate("SELECT GROUP_CONCAT(DISTINCT model) FROM t")
        assert "string_agg(DISTINCT model, ',')" in s

    def test_literal_percent_escaped(self):
        # LIKE wildcards must survive psycopg (literal % -> %%), placeholders stay %s
        s, _ = pg._translate("WHERE name LIKE ? || '%'")
        assert "LIKE %s || '%%'" in s


@pytest.mark.skipif(not os.environ.get("PROMPTRY_POSTGRES_DSN"),
                    reason="PROMPTRY_POSTGRES_DSN not set")
class TestLiveBackend:
    @pytest.fixture
    def storage(self):
        dsn = os.environ["PROMPTRY_POSTGRES_DSN"]
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE")
            c.execute("CREATE SCHEMA public")
        st = pg.PostgresStorage(dsn=dsn)
        yield st
        st.close()

    def test_dedup_versioning_and_lastrowid(self, storage):
        r1 = storage.save_prompt("p", "v1", "h1")
        assert r1.id and r1.version == 1                       # RETURNING id worked
        r2 = storage.save_prompt("p", "v1", "h1")
        assert r2.version == 1                                 # (name,hash) dedup
        assert storage.save_prompt("p", "v2", "h2").version == 2

    def test_response_id_dedup(self, storage):
        a = storage.record_invocation("p", metadata={"cost": 0.01, "model": "gpt-4o"},
                                      response_id="rid")
        b = storage.record_invocation("p", metadata={"cost": 0.02, "model": "gpt-4o"},
                                      response_id="rid")
        assert a > 0 and b == 0 and storage.count_invocations() == 1

    def test_cost_aggregation(self, storage):
        storage.record_invocation("p", metadata={"cost": 0.01, "model": "gpt-4o",
                                                  "tokens_in": 100, "cached_tokens": 10})
        s = storage.get_cost_data(days=7)["summary"]
        assert s["total_cost"] == pytest.approx(0.01)
        assert s["total_tokens_in"] == 100 and s["total_cached_tokens"] == 10
