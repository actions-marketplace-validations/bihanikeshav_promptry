import pytest


class TestPromptStorage:

    def test_basic_save(self, storage):
        record = storage.save_prompt("test", "Hello world", "abc123")
        assert record.name == "test"
        assert record.version == 1
        assert record.content == "Hello world"
        assert record.hash == "abc123"

    def test_auto_increment_version(self, storage):
        r1 = storage.save_prompt("test", "Version 1", "hash1")
        r2 = storage.save_prompt("test", "Version 2", "hash2")
        assert r1.version == 1
        assert r2.version == 2

    def test_dedup_same_content(self, storage):
        r1 = storage.save_prompt("test", "Same content", "same_hash")
        r2 = storage.save_prompt("test", "Same content", "same_hash")
        assert r1.version == r2.version
        assert r1.id == r2.id

    def test_metadata_roundtrip(self, storage):
        storage.save_prompt("test", "Content", "h1", metadata={"author": "keshav"})
        fetched = storage.get_prompt("test", 1)
        assert fetched.metadata == {"author": "keshav"}

    def test_get_latest(self, storage):
        storage.save_prompt("test", "V1", "h1")
        storage.save_prompt("test", "V2", "h2")
        latest = storage.get_prompt("test")
        assert latest.version == 2
        assert latest.content == "V2"

    def test_get_specific_version(self, storage):
        storage.save_prompt("test", "V1", "h1")
        storage.save_prompt("test", "V2", "h2")
        v1 = storage.get_prompt("test", 1)
        assert v1.content == "V1"

    def test_get_nonexistent(self, storage):
        assert storage.get_prompt("nope") is None

    def test_list_all(self, storage):
        storage.save_prompt("a", "Content A", "ha")
        storage.save_prompt("b", "Content B", "hb")
        records = storage.list_prompts()
        assert len(records) == 2
        assert {r.name for r in records} == {"a", "b"}

    def test_list_by_name(self, storage):
        storage.save_prompt("a", "V1", "h1")
        storage.save_prompt("a", "V2", "h2")
        storage.save_prompt("b", "Other", "h3")
        records = storage.list_prompts("a")
        assert len(records) == 2
        assert all(r.name == "a" for r in records)

    def test_tagging(self, storage):
        record = storage.save_prompt("test", "Content", "h1")
        storage.tag_prompt(record.id, "prod")
        assert "prod" in storage.get_tags(record.id)

    def test_duplicate_tag_ignored(self, storage):
        record = storage.save_prompt("test", "Content", "h1")
        storage.tag_prompt(record.id, "prod")
        storage.tag_prompt(record.id, "prod")
        assert storage.get_tags(record.id).count("prod") == 1

    def test_get_by_tag(self, storage):
        storage.save_prompt("test", "V1", "h1")
        r2 = storage.save_prompt("test", "V2", "h2")
        storage.tag_prompt(r2.id, "prod")
        found = storage.get_prompt_by_tag("test", "prod")
        assert found.version == 2


class TestPromptPagination:

    def test_list_prompts_limit(self, storage):
        for i in range(5):
            storage.save_prompt(f"p{i}", f"content {i}", f"hash{i}")
        records = storage.list_prompts(limit=2)
        assert len(records) == 2

    def test_list_prompts_offset(self, storage):
        for i in range(5):
            storage.save_prompt(f"p{i}", f"content {i}", f"hash{i}")
        all_records = storage.list_prompts()
        page = storage.list_prompts(offset=2, limit=2)
        assert len(page) == 2
        assert page[0].name == all_records[2].name
        assert page[1].name == all_records[3].name

    def test_list_prompts_offset_past_end(self, storage):
        storage.save_prompt("a", "content", "h1")
        records = storage.list_prompts(offset=10)
        assert records == []


class TestEvalPagination:

    def test_get_eval_runs_limit(self, storage):
        for i in range(5):
            storage.save_eval_run(suite_name="s1", overall_pass=True, overall_score=0.8 + i * 0.01)
        runs = storage.get_eval_runs("s1", limit=3)
        assert len(runs) == 3

    def test_get_eval_runs_offset(self, storage):
        for i in range(5):
            storage.save_eval_run(suite_name="s1", overall_pass=True, overall_score=0.8 + i * 0.01)
        all_runs = storage.get_eval_runs("s1")
        page = storage.get_eval_runs("s1", offset=2, limit=2)
        assert len(page) == 2
        assert page[0].id == all_runs[2].id


class TestVotePagination:

    def test_get_votes_offset(self, storage):
        for i in range(5):
            storage.save_vote(prompt_name="p", response=f"resp {i}", score=1)
        all_votes = storage.get_votes(prompt_name="p")
        page = storage.get_votes(prompt_name="p", offset=1, limit=2)
        assert len(page) == 2
        assert page[0]["id"] == all_votes[1]["id"]


class TestEvalStorage:

    def test_save_run(self, storage):
        run_id = storage.save_eval_run(
            suite_name="regression",
            prompt_name="test",
            prompt_version=1,
            overall_pass=True,
            overall_score=0.95,
        )
        assert run_id > 0

    def test_save_and_fetch_results(self, storage):
        run_id = storage.save_eval_run(suite_name="regression", overall_pass=True)
        result_id = storage.save_eval_result(
            run_id=run_id,
            test_name="test_qa",
            assertion_type="semantic",
            passed=True,
            score=0.91,
            details={"threshold": 0.8},
            latency_ms=150.0,
        )
        assert result_id > 0

        results = storage.get_eval_results(run_id)
        assert len(results) == 1
        assert results[0].test_name == "test_qa"
        assert results[0].score == 0.91
        assert results[0].details == {"threshold": 0.8}

    def test_runs_ordered_newest_first(self, storage):
        storage.save_eval_run(suite_name="s1", overall_pass=True, overall_score=0.9)
        storage.save_eval_run(suite_name="s1", overall_pass=False, overall_score=0.7)
        runs = storage.get_eval_runs("s1")
        assert len(runs) == 2
        assert runs[0].overall_score == 0.7  # newest first

    def test_score_history(self, storage):
        storage.save_eval_run(suite_name="s1", overall_pass=True, overall_score=0.9)
        storage.save_eval_run(suite_name="s1", overall_pass=True, overall_score=0.85)
        history = storage.get_score_history("s1")
        assert len(history) == 2
        assert all(isinstance(s, float) for _, s in history)


class TestSuiteNames:

    def test_list_suite_names_empty(self, storage):
        assert storage.list_suite_names() == []

    def test_list_suite_names(self, storage):
        storage.save_eval_run(suite_name="alpha", overall_pass=True, overall_score=0.9)
        storage.save_eval_run(suite_name="beta", overall_pass=True, overall_score=0.8)
        storage.save_eval_run(suite_name="alpha", overall_pass=True, overall_score=0.85)
        names = storage.list_suite_names()
        assert sorted(names) == ["alpha", "beta"]


class TestGetRunById:

    def test_get_existing_run(self, storage):
        run_id = storage.save_eval_run(suite_name="test", overall_pass=True, overall_score=0.9)
        run = storage.get_eval_run_by_id(run_id)
        assert run is not None
        assert run.id == run_id
        assert run.suite_name == "test"

    def test_get_nonexistent_run(self, storage):
        assert storage.get_eval_run_by_id(9999) is None


class TestGetCostData:

    def test_cost_data_empty(self, storage):
        result = storage.get_cost_data(days=7)
        assert result["summary"]["total_calls"] == 0
        assert result["by_name"] == []

    def test_cost_data_with_metadata(self, storage):
        # Cost lives in the invocations ledger (one row per call), not in
        # prompt-version metadata.
        storage.record_invocation("my-prompt", metadata={"tokens_in": 500, "tokens_out": 100, "model": "gpt-4o", "cost": 0.005})
        storage.record_invocation("my-prompt", metadata={"tokens_in": 300, "tokens_out": 50, "model": "gpt-4o", "cost": 0.003})
        result = storage.get_cost_data(days=7)
        assert result["summary"]["total_calls"] == 2
        assert result["summary"]["total_cost"] == pytest.approx(0.008)
        assert len(result["by_name"]) == 1
        assert result["by_name"][0]["name"] == "my-prompt"
        assert result["by_name"][0]["tokens_in"] == 800

    def test_cost_data_filter_by_name(self, storage):
        storage.record_invocation("a", metadata={"cost": 0.01})
        storage.record_invocation("b", metadata={"cost": 0.02})
        result = storage.get_cost_data(days=7, name="a")
        assert len(result["by_name"]) == 1
        assert result["by_name"][0]["name"] == "a"

    def test_cost_data_filter_by_model(self, storage):
        storage.record_invocation("p", metadata={"cost": 0.01, "model": "gpt-4o"})
        storage.record_invocation("p", metadata={"cost": 0.02, "model": "claude"})
        result = storage.get_cost_data(days=7, model="gpt-4o")
        assert result["summary"]["total_cost"] == pytest.approx(0.01)

    def test_cost_data_recomputes_rerouted_slug(self, storage):
        # A retired slug (xAI grok-4-1-fast-non-reasoning, rerouted to grok-4.3
        # on/after 2026-05-15) must NOT be trusted at its stored cost: the
        # dashboard recomputes from tokens at the replacement's rate. Stored
        # cost is deliberately absurd (999.0) to prove the recompute wins in
        # both by_name and the summary. Rows default to today's date, which is
        # past the reroute cutoff.
        from promptry import pricing
        model = "grok-4-1-fast-non-reasoning"
        assert model in pricing.REROUTES  # guards the fixture's premise
        storage.record_invocation(
            "rr", metadata={"model": model, "tokens_in": 1000,
                            "tokens_out": 500, "cost": 999.0})
        expected = pricing.calculate_cost(
            model, tokens_in=1000, tokens_out=500, when="2026-07-07")
        assert expected is not None and expected < 1.0  # sanity: cheap, not 999
        result = storage.get_cost_data(days=7)
        assert result["by_name"][0]["cost"] == pytest.approx(expected)
        assert result["summary"]["total_cost"] == pytest.approx(expected)


# ---- SQL-aggregation equivalence (post-migration) ----
# These pin the exact output shapes/values that the JSON-scan implementations
# produced (snapshot captured from the pre-rewrite code) so the SQL-side
# rewrite stays byte-compatible with what the dashboard consumes.

def _seed_mixed(storage):
    rows = [
        ("mod.a", {"tokens_in": 500, "tokens_out": 100, "model": "gpt-4o", "cost": 0.005, "latency_ms": 120.0, "cached_tokens": 200, "cache_write_tokens": 10}),
        ("mod.a", {"tokens_in": 300, "tokens_out": 50, "model": "gpt-4o", "cost": 0.003, "latency_ms": 90.5}),
        ("mod.b", {"prompt_tokens": 400, "completion_tokens": 80, "model": "claude-3-5-sonnet-20241022", "cost": 0.012, "latency_ms": 200.0, "cached_tokens": 100}),
        ("mod.b", {"input_tokens": 250, "output_tokens": 40, "model": "gpt-4o", "cost": 0.002}),
        ("other.c", {"tokens_in": 1000, "tokens_out": 200, "model": "claude-3-5-sonnet-20241022", "cost": 0.02, "latency_ms": 300.0}),
    ]
    ids = [storage.record_invocation(n, metadata=m) for n, m in rows]
    return ids


class TestSqlAggregationEquivalence:

    def test_get_cost_data_all(self, storage):
        _seed_mixed(storage)
        r = storage.get_cost_data(days=7)
        s = r["summary"]
        assert s["total_calls"] == 5
        assert s["total_cost"] == pytest.approx(0.042)
        assert s["total_tokens_in"] == 2450
        assert s["total_tokens_out"] == 470
        assert s["total_cached_tokens"] == 300
        assert s["total_cache_write_tokens"] == 10
        assert s["cache_hit_rate"] == pytest.approx(0.12244897959183673)
        assert s["cache_savings"] == pytest.approx(0.00025)
        assert s["uncached_cost"] == pytest.approx(0.04225)
        assert s["avg_cost"] == pytest.approx(0.0084)

        by_name = {e["name"]: e for e in r["by_name"]}
        # sorted by cost desc
        assert [e["name"] for e in r["by_name"]] == ["other.c", "mod.b", "mod.a"]
        a = by_name["mod.a"]
        assert a["calls"] == 2 and a["tokens_in"] == 800 and a["tokens_out"] == 150
        assert a["cached_tokens"] == 200 and a["cache_write_tokens"] == 10
        assert a["cost"] == pytest.approx(0.008)
        assert a["cache_savings"] == pytest.approx(0.00025)
        assert a["cache_hit_rate"] == pytest.approx(0.25)
        assert a["models"] == ["gpt-4o"]
        b = by_name["mod.b"]
        assert b["models"] == ["claude-3-5-sonnet-20241022", "gpt-4o"]
        assert b["cache_savings"] == pytest.approx(0.0)

        assert len(r["by_date"]) == 1
        d = r["by_date"][0]
        assert d["calls"] == 5 and d["tokens_in"] == 2450 and d["tokens_out"] == 470
        assert d["cached_tokens"] == 300 and d["cost"] == pytest.approx(0.042)

    def test_get_cost_data_named(self, storage):
        _seed_mixed(storage)
        r = storage.get_cost_data(days=7, name="mod.a")
        assert r["summary"]["total_calls"] == 2
        assert r["summary"]["total_cost"] == pytest.approx(0.008)
        assert [e["name"] for e in r["by_name"]] == ["mod.a"]

    def test_get_cost_data_model(self, storage):
        _seed_mixed(storage)
        r = storage.get_cost_data(days=7, model="gpt-4o")
        assert r["summary"]["total_calls"] == 3
        assert r["summary"]["total_cost"] == pytest.approx(0.01)
        assert r["summary"]["total_tokens_in"] == 1050
        assert r["summary"]["total_tokens_out"] == 190
        assert [e["name"] for e in r["by_name"]] == ["mod.a", "mod.b"]

    def test_get_invocation_stats(self, storage):
        _seed_mixed(storage)
        r = storage.get_invocation_stats("mod.a", days=30)
        assert r["count"] == 2
        m = r["metrics"]
        assert m["tokens_in"] == {"min": 300.0, "avg": 400.0, "p50": 400.0, "p95": 490.0, "max": 500.0, "sum": 800.0}
        assert m["cost"]["sum"] == pytest.approx(0.008)
        assert m["latency_ms"]["avg"] == pytest.approx(105.25)
        assert m["latency_ms"]["p95"] == pytest.approx(118.525)
        assert len(r["histogram"]) == 12
        assert r["histogram"][0] == {"start": 300, "end": 317, "count": 1}
        assert r["histogram"][-1] == {"start": 483, "end": 500, "count": 1}

    def test_get_budget_status_one_pass(self, storage):
        _seed_mixed(storage)
        storage.save_budget("global", "daily", 0.1)
        storage.save_budget("module", "monthly", 0.05, target="mod")
        storage.save_budget("prompt", "daily", 0.01, target="mod.a")
        out = {b["scope"]: b for b in storage.get_budget_status()}
        assert out["global"]["spend"] == pytest.approx(0.042)
        assert out["global"]["pct"] == pytest.approx(42.0)
        assert out["global"]["breached"] is False
        assert out["module"]["spend"] == pytest.approx(0.022)
        assert out["module"]["pct"] == pytest.approx(44.0)
        assert out["prompt"]["spend"] == pytest.approx(0.008)
        assert out["prompt"]["pct"] == pytest.approx(80.0)

    def test_get_invocation_models(self, storage):
        _seed_mixed(storage)
        assert storage.get_invocation_models(days=30) == [
            {"model": "gpt-4o", "calls": 3},
            {"model": "claude-3-5-sonnet-20241022", "calls": 2},
        ]

    def test_list_invocations_ordering(self, storage):
        _seed_mixed(storage)
        recent = storage.list_invocations(days=7, order="recent")
        assert [r["id"] for r in recent] == [5, 4, 3, 2, 1]
        cost = storage.list_invocations(days=7, order="cost")
        assert [r["id"] for r in cost] == [5, 3, 1, 2, 4]
        # Anthropic-style spelling still surfaces as null per-row (unchanged)
        row4 = next(r for r in recent if r["id"] == 4)
        assert row4["tokens_in"] is None and row4["cost"] == pytest.approx(0.002)

    def test_get_model_cost_summary(self, storage):
        _seed_mixed(storage)
        s = storage.get_model_cost_summary("gpt-4o", days=3650)
        assert s["calls"] == 3
        assert s["cost"] == pytest.approx(0.01)
        # latency only present on 2 of 3 gpt-4o rows (120.0, 90.5)
        assert s["avg_latency"] == pytest.approx(105.25)
        empty = storage.get_model_cost_summary("nonexistent", days=3650)
        assert empty == {"cost": 0.0, "calls": 0, "avg_latency": 0.0}
