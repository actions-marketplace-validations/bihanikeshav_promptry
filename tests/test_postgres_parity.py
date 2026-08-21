"""Parity + correctness tests for the Postgres scale-tier backend.

These run only when PROMPTRY_POSTGRES_DSN points at a reachable Postgres (the
CI 'postgres' service container sets it; locally, export it against any test
database). Without it the whole module is skipped, so the default SQLite-only
developer never needs Postgres installed.

The point is to exercise the SQL-dialect translator and the schema against a
*real* server — json_extract casts, datetime windows, GROUP_CONCAT, INSERT OR
IGNORE, RETURNING-id emulation, atomic transactions, and concurrent access
through the connection pool.
"""
from __future__ import annotations

import os
import threading

import pytest

DSN = os.environ.get("PROMPTRY_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PROMPTRY_POSTGRES_DSN not set")

# All application tables, in an order safe to TRUNCATE ... CASCADE.
_TABLES = [
    "eval_results", "eval_runs", "prompt_tags", "prompts", "votes", "datasets",
    "invocations", "feedback", "golden_examples", "budgets", "user_identities",
    "audit_log", "users",
]


@pytest.fixture
def pg():
    from promptry.storage.postgres import PostgresStorage
    s = PostgresStorage(DSN)
    # Clean slate per test (schema already created by __init__).
    s._conn.raw("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(_TABLES))
    s._conn.commit()
    yield s
    s.close()


def test_schema_and_prompt_roundtrip(pg):
    rec = pg.save_prompt("greet", "Hello {name}", "h1", metadata={"a": 1})
    assert rec.version == 1
    got = pg.get_prompt("greet")
    assert got.content == "Hello {name}" and got.metadata == {"a": 1}
    # a second, different version bumps
    rec2 = pg.save_prompt("greet", "Hi {name}", "h2")
    assert rec2.version == 2


def test_eval_run_atomicity_rolls_back_on_bad_result(pg):
    # save_eval_run_atomic must be all-or-nothing (the Postgres override wraps
    # both inserts in one real transaction despite the autocommit connection).
    good = [{"test_name": "t", "assertion_type": "contains", "passed": True,
             "score": 1.0, "details": {"x": 1}, "latency_ms": 1.0}]
    rid = pg.save_eval_run_atomic(results=good, suite_name="s", overall_pass=True,
                                  overall_score=1.0)
    assert rid > 0
    runs_before = len(pg.get_eval_runs("s"))

    # A result row that violates NOT NULL (assertion_type=None) must abort the
    # whole run — no orphan eval_runs row left behind.
    bad = [{"test_name": "t", "assertion_type": None, "passed": True,
            "score": 1.0, "details": None, "latency_ms": 1.0}]
    with pytest.raises(Exception):
        pg.save_eval_run_atomic(results=bad, suite_name="s", overall_pass=True,
                                overall_score=1.0)
    # The failed run left nothing partial: still exactly one run for 's'.
    assert len(pg.get_eval_runs("s")) == runs_before


def test_cost_aggregation_json_casts(pg):
    for i in range(3):
        pg.record_invocation(
            "greet",
            metadata={"model": "gpt-4o", "cost": 0.01, "prompt_tokens": 100,
                      "completion_tokens": 20, "latency_ms": 12.0},
            input_text="a", output_text="b", request_id=f"r{i}",
        )
    cost = pg.get_cost_data(days=30)
    assert cost["summary"]["total_cost"] == pytest.approx(0.03, abs=1e-9)
    models = pg.get_invocation_models(days=30)
    assert any(m.get("model") == "gpt-4o" for m in models)
    summ = pg.get_model_cost_summary("gpt-4o", days=30)
    assert summ["calls"] == 3


def test_trace_json_extract(pg):
    pg.record_invocation(
        "greet",
        metadata={"model": "gpt-4o", "cost": 0.02, "trace_id": "T1",
                  "span_name": "root", "latency_ms": 5.0},
        input_text="a", output_text="b", request_id="req-trace",
    )
    traces = pg.list_traces(days=30)
    assert any(t.get("trace_id") == "T1" for t in traces)
    spans = pg.get_trace("T1")
    assert len(spans) == 1


def test_feedback_stats_window(pg):
    pg.record_invocation("greet", metadata={"model": "m"}, input_text="a",
                         output_text="b", request_id="fb1")
    pg.save_feedback("fb1", rating=1.0, comment="great", source="user")
    stats = pg.get_feedback_stats(days=30)
    assert stats["total"] >= 1


def test_bisect_and_drift(pg):
    for passed in (True, False, False):
        pg.save_eval_run(suite_name="d", overall_pass=passed,
                         overall_score=1.0 if passed else 0.0)
    out = pg.bisect_regression("d")
    assert out["found"] is True


def test_users_password_bump_and_audit_exemption(pg):
    u = pg.create_user("a@b.com", password_hash="x", role="admin")
    assert pg.get_user_by_id(u["id"]).get("token_version") == 0
    pg.update_user(u["id"], password_hash="y")
    assert pg.get_user_by_id(u["id"]).get("token_version") == 1  # bumped

    pg.record_audit("golden.add")                    # routine -> prunable
    pg.record_audit("auth.login", result="ok")       # security -> kept
    pg.record_audit("auth.login", result="denied")   # failure -> kept
    pg._conn.raw("UPDATE audit_log SET ts = to_char(now() - interval '400 days',"
                 " 'YYYY-MM-DD HH24:MI:SS')")
    pg._conn.commit()
    assert pg.purge_old_audit(365) == 1              # only golden.add
    kept = {e["action"] + ":" + e["result"] for e in pg.list_audit(limit=50)}
    assert "auth.login:ok" in kept and "auth.login:denied" in kept


def test_budgets_and_golden(pg):
    pg.save_budget(scope="global", period="month", limit_usd=100.0)
    assert len(pg.list_budgets()) == 1
    assert isinstance(pg.get_budget_status(), list)
    pg.add_golden_example("greet", "2+2", "4")
    assert len(pg.list_golden_examples("greet")) == 1


def test_concurrent_writes_and_reads(pg):
    # With the connection pool, N threads should make progress concurrently
    # without "connection already in use" / cross-thread cursor errors.
    errors: list = []

    def worker(n: int):
        try:
            for i in range(5):
                pg.record_invocation(
                    f"p{n}", metadata={"model": "m", "cost": 0.001},
                    input_text="x", output_text="y", request_id=f"c{n}-{i}",
                )
                pg.get_cost_data(days=30)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    assert pg.count_invocations() == 40
