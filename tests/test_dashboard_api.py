"""Tests for the dashboard FastAPI server."""
import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from promptry.storage.sqlite import SQLiteStorage


def _seed_suite(storage, suite_name, scores, model="gpt-4o", prompt_version=1):
    """Seed eval runs and results for a suite."""
    for score in scores:
        run_id = storage.save_eval_run(
            suite_name=suite_name,
            model_version=model,
            prompt_name="test-prompt",
            prompt_version=prompt_version,
            overall_pass=score >= 0.7,
            overall_score=score,
        )
        storage.save_eval_result(
            run_id=run_id,
            test_name="test_main",
            assertion_type="semantic",
            passed=score >= 0.7,
            score=score,
        )
    return run_id  # return last run_id


def _seed_prompt(storage, name, contents):
    """Seed prompt versions."""
    import hashlib

    records = []
    for content in contents:
        h = hashlib.sha256(content.encode()).hexdigest()[:16]
        record = storage.save_prompt(name=name, content=content, content_hash=h)
        records.append(record)
    return records


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(db_path=tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def client(storage):
    import promptry.dashboard.server as srv

    original = srv.get_storage
    srv.get_storage = lambda: storage
    from promptry.dashboard.server import app

    with TestClient(app) as c:
        yield c
    srv.get_storage = original


# ---- Health ----

class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "db_path" in data


# ---- Suites ----

class TestSuites:
    def test_suites_empty(self, client):
        resp = client.get("/api/suites")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_suites_with_data(self, client, storage):
        _seed_suite(storage, "qa-suite", [0.8, 0.85, 0.9])
        resp = client.get("/api/suites")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        s = data[0]
        assert s["name"] == "qa-suite"
        assert s["latest_score"] == pytest.approx(0.9)
        assert s["passed"] is True
        assert "drift_status" in s
        assert "drift_slope" in s
        assert s["model_version"] == "gpt-4o"
        assert s["prompt_version"] == 1
        assert "timestamp" in s
        assert "sparkline_scores" in s
        # sparkline should be oldest-first
        assert len(s["sparkline_scores"]) == 3


# ---- Suite Runs ----

class TestSuiteRuns:
    def test_suite_runs(self, client, storage):
        _seed_suite(storage, "qa-suite", [0.8, 0.85])
        resp = client.get("/api/suite/qa-suite/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # newest first
        assert data[0]["overall_score"] == pytest.approx(0.85)

    def test_suite_runs_with_limit(self, client, storage):
        _seed_suite(storage, "qa-suite", [0.7, 0.75, 0.8, 0.85])
        resp = client.get("/api/suite/qa-suite/runs?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


# ---- Run Detail ----

class TestRunDetail:
    def test_run_detail(self, client, storage):
        run_id = _seed_suite(storage, "qa-suite", [0.9])
        resp = client.get(f"/api/suite/qa-suite/run/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run"]["suite_name"] == "qa-suite"
        assert len(data["assertions"]) == 1
        assert data["assertions"][0]["test_name"] == "test_main"

    def test_run_detail_wrong_suite(self, client, storage):
        run_id = _seed_suite(storage, "qa-suite", [0.9])
        resp = client.get(f"/api/suite/other-suite/run/{run_id}")
        assert resp.status_code == 404

    def test_run_detail_nonexistent(self, client, storage):
        resp = client.get("/api/suite/qa-suite/run/9999")
        assert resp.status_code == 404


# ---- Prompts ----

class TestPromptsList:
    def test_prompts_empty(self, client):
        resp = client.get("/api/prompts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_prompts_with_data(self, client, storage):
        _seed_prompt(storage, "summarizer", ["v1 content", "v2 content"])
        resp = client.get("/api/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "summarizer"
        assert data[0]["latest_version"] == 2


    def test_prompts_pagination(self, client, storage):
        for i in range(5):
            _seed_prompt(storage, f"prompt-{i}", [f"content {i}"])
        resp = client.get("/api/prompts?offset=0&limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        resp2 = client.get("/api/prompts?offset=2&limit=2")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2) == 2
        # Pages should not overlap
        names_page1 = {p["name"] for p in data}
        names_page2 = {p["name"] for p in data2}
        assert names_page1.isdisjoint(names_page2)


class TestPromptVersions:
    def test_prompt_versions(self, client, storage):
        records = _seed_prompt(storage, "summarizer", ["v1", "v2", "v3"])
        resp = client.get("/api/prompts/summarizer")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["versions"]) == 3

    def test_prompt_versions_not_found(self, client):
        resp = client.get("/api/prompts/nonexistent")
        assert resp.status_code == 404


class TestPromptContent:
    def test_prompt_content(self, client, storage):
        _seed_prompt(storage, "summarizer", ["v1 content", "v2 content"])
        resp = client.get("/api/prompts/summarizer/content?v=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "v1 content"
        assert data["version"] == 1

    def test_prompt_content_latest(self, client, storage):
        _seed_prompt(storage, "summarizer", ["v1 content", "v2 content"])
        resp = client.get("/api/prompts/summarizer/content")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "v2 content"

    def test_prompt_content_not_found(self, client):
        resp = client.get("/api/prompts/nonexistent/content")
        assert resp.status_code == 404


class TestPromptDiff:
    def test_prompt_diff(self, client, storage):
        _seed_prompt(storage, "summarizer", [
            "line1\nline2\nline3",
            "line1\nmodified\nline3\nline4",
        ])
        resp = client.get("/api/prompts/summarizer/diff?v1=1&v2=2")
        assert resp.status_code == 200
        data = resp.json()
        assert "additions" in data
        assert "deletions" in data
        assert "lines" in data
        assert data["additions"] >= 1
        assert data["deletions"] >= 1
        # Check line types
        types = {line["type"] for line in data["lines"]}
        assert "unchanged" in types or "added" in types or "deleted" in types


# ---- Models ----

class TestModelVersions:
    def test_model_versions(self, client, storage):
        _seed_suite(storage, "qa-suite", [0.8, 0.85], model="gpt-4o")
        _seed_suite(storage, "qa-suite", [0.9], model="claude-sonnet")
        resp = client.get("/api/models/qa-suite")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["versions"]) == 2
        models = {v["model_version"] for v in data["versions"]}
        assert "gpt-4o" in models
        assert "claude-sonnet" in models


class TestModelCompare:
    def test_model_compare(self, client, storage):
        _seed_suite(storage, "qa-suite", [0.8, 0.82, 0.85, 0.83, 0.84], model="gpt-4o")
        _seed_suite(storage, "qa-suite", [0.9], model="claude-sonnet")
        resp = client.get(
            "/api/models/qa-suite/compare?baseline=gpt-4o&candidate=claude-sonnet"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["suite_name"] == "qa-suite"
        assert data["baseline"]["model_version"] == "gpt-4o"
        assert data["candidate"]["model_version"] == "claude-sonnet"
        assert "verdict" in data
        assert "overall_delta" in data


# ---- Cost ----

class TestCost:
    def test_cost_empty(self, client):
        resp = client.get("/api/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_calls"] == 0

    def test_cost_with_data(self, client, storage):
        # Cost comes from the invocations ledger (one row per call).
        for _ in range(3):
            storage.record_invocation(
                "cost-prompt",
                metadata={"model": "gpt-4o", "cost": 0.05, "tokens_in": 100, "tokens_out": 50},
            )

        resp = client.get("/api/cost?days=7&name=cost-prompt&model=gpt-4o")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_calls"] == 3
        assert data["summary"]["total_cost"] == pytest.approx(0.15)


# ---- Feedback ----


# ---- Config save (provenance) ----

class TestConfigSave:
    """The dashboard's Settings page writes to the legacy
    .promptry/config.toml (see promptry/projectconfig.py). The save path must
    only ever persist fields it's actually setting, on top of whatever is
    already in that legacy file — never the merged view that also carries
    values from ~/.promptry/config.toml or the canonical promptry.toml,
    otherwise those get copied into the committed legacy file and/or silently
    shadow the canonical file on next read."""

    @pytest.fixture(autouse=True)
    def _isolate_cwd(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.chdir(tmp_path)
        from promptry.projectconfig import reset_project_config
        reset_project_config()
        yield
        reset_project_config()

    def _write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_save_does_not_copy_home_or_canonical_keys_into_legacy_file(
        self, client, tmp_path
    ):
        # A personal home-level fallback and a team-committed promptry.toml
        # both contribute keys to the merged view...
        self._write(
            tmp_path / "home" / ".promptry" / "config.toml",
            '[judge]\nmodel = "home-model"\n',
        )
        self._write(
            tmp_path / "promptry.toml",
            '[judge]\nmodel = "canonical-model"\nmax_prompt_chars = 4000\n'
            '[pricing.canonical-model]\nin = 1.0\nout = 2.0\n',
        )
        # ... but the dashboard only touches "dashboard" here.
        resp = client.post("/api/config", json={"dashboard": {"default_days": 21}})
        assert resp.status_code == 200

        legacy_path = tmp_path / ".promptry" / "config.toml"
        raw_text = legacy_path.read_text(encoding="utf-8")
        # None of the home/canonical-sourced values should have been copied in.
        assert "home-model" not in raw_text
        assert "canonical-model" not in raw_text
        assert "max_prompt_chars" not in raw_text
        assert "pricing" not in raw_text

    def test_save_preserves_preexisting_legacy_keys_not_touched_by_post(
        self, client, tmp_path
    ):
        self._write(
            tmp_path / ".promptry" / "config.toml",
            '[pricing.my-model]\nin = 1.0\nout = 2.0\n',
        )
        resp = client.post("/api/config", json={"judge": {"model": "new-judge"}})
        assert resp.status_code == 200

        from promptry.projectconfig import load_raw_config
        raw = load_raw_config()
        assert raw["judge"]["model"] == "new-judge"
        # pre-existing legacy key untouched by this POST survives the rewrite
        assert raw["pricing"]["my-model"] == {"in": 1.0, "out": 2.0}


class TestFeedback:
    def _seed(self, storage):
        # 3 positive, 2 negative (one with a note); link each to an invocation
        ratings = [(0.95, None), (0.9, "great"), (0.8, None), (0.4, "wrong policy"), (0.2, None)]
        for i, (rating, comment) in enumerate(ratings):
            rid = f"req-{i}"
            storage.record_invocation(
                "rag.answer",
                metadata={"model": "gpt-4o-mini", "cost": 0.001, "tokens_in": 900, "tokens_out": 80},
                prompt_version=1, request_id=rid,
            )
            storage.save_feedback(rid, rating=rating, comment=comment, source="web-widget")

    def test_list_links_to_invocation(self, client, storage):
        self._seed(storage)
        resp = client.get("/api/feedback")
        assert resp.status_code == 200
        rows = resp.json()["feedback"]
        assert len(rows) == 5
        # newest-first, each carries the invocation id + model for the trace link
        assert rows[0]["invocation_id"] is not None
        assert rows[0]["model"] == "gpt-4o-mini"

    def test_filters(self, client, storage):
        self._seed(storage)
        assert len(client.get("/api/feedback?only_comments=true").json()["feedback"]) == 2
        assert len(client.get("/api/feedback?min_rating=0.5").json()["feedback"]) == 2  # the two negatives

    def test_pagination(self, client, storage):
        self._seed(storage)
        page1 = client.get("/api/feedback?limit=2&offset=0").json()["feedback"]
        page2 = client.get("/api/feedback?limit=2&offset=2").json()["feedback"]
        assert len(page1) == 2 and len(page2) == 2
        assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})  # no overlap

    def test_search(self, client, storage):
        self._seed(storage)
        rows = client.get("/api/feedback?q=policy").json()["feedback"]
        assert len(rows) == 1 and "policy" in rows[0]["comment"]

    def test_stats(self, client, storage):
        self._seed(storage)
        data = client.get("/api/feedback/stats").json()
        assert data["total"] == 5 and data["rated"] == 5
        assert data["positive"] == 3 and data["negative"] == 2
        assert data["positive_rate"] == pytest.approx(0.6)
        assert data["with_comments"] == 2
        assert data["by_prompt"][0]["name"] == "rag.answer"

    def test_empty(self, client):
        data = client.get("/api/feedback/stats").json()
        assert data["total"] == 0 and data["positive_rate"] is None
        assert client.get("/api/feedback").json()["feedback"] == []
