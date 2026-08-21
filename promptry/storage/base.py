"""Abstract storage interface.

Implement this to use a different backend (Postgres, Mongo, etc.).
The only requirement is that your backend can handle the methods below.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from promptry.models import PromptRecord, EvalRunRecord, EvalResultRecord


class BaseStorage(ABC):

    # ---- prompts ----

    @abstractmethod
    def save_prompt(self, name, content, content_hash, metadata=None, force=False) -> PromptRecord:
        ...

    @abstractmethod
    def get_prompt(self, name, version=None) -> PromptRecord | None:
        ...

    @abstractmethod
    def get_prompt_by_tag(self, name, tag) -> PromptRecord | None:
        ...

    @abstractmethod
    def list_prompts(self, name=None, offset=0, limit=100) -> list[PromptRecord]:
        ...

    @abstractmethod
    def tag_prompt(self, prompt_id, tag):
        ...

    @abstractmethod
    def get_tags(self, prompt_id) -> list[str]:
        ...

    # ---- eval runs ----

    @abstractmethod
    def save_eval_run(
        self,
        suite_name,
        prompt_name=None,
        prompt_version=None,
        model_version=None,
        overall_pass=True,
        overall_score=None,
    ) -> int:
        ...

    @abstractmethod
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
        ...

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
        """Persist an eval run *and* all its result rows as one unit.

        The run row and its per-assertion result rows must land together: a
        run persisted without its results shows up on the dashboard as a
        "passing" run with no detail (a corrupt audit trail). Backends that
        support transactions override this to make it atomic; this default
        keeps the ordering in one place for backends that don't.

        ``results`` is a list of dicts with keys ``test_name``,
        ``assertion_type``, ``passed`` and optional ``score``, ``details``,
        ``latency_ms``. Returns the new run's id.
        """
        run_id = self.save_eval_run(
            suite_name=suite_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            model_version=model_version,
            overall_pass=overall_pass,
            overall_score=overall_score,
        )
        for r in results:
            self.save_eval_result(
                run_id=run_id,
                test_name=r["test_name"],
                assertion_type=r["assertion_type"],
                passed=r["passed"],
                score=r.get("score"),
                details=r.get("details"),
                latency_ms=r.get("latency_ms"),
            )
        return run_id

    @abstractmethod
    def get_eval_runs(self, suite_name, offset=0, limit=50) -> list[EvalRunRecord]:
        ...

    @abstractmethod
    def get_eval_runs_batch(self, suite_names: list[str], limit_per_suite: int = 20) -> dict[str, list[EvalRunRecord]]:
        """Fetch eval runs for multiple suites in one query.

        Returns a dict mapping suite_name -> list of EvalRunRecord,
        with at most limit_per_suite runs per suite (newest first).
        """
        ...

    @abstractmethod
    def get_eval_results(self, run_id) -> list[EvalResultRecord]:
        ...

    @abstractmethod
    def get_eval_results_batch(self, run_ids: list[int]) -> dict[int, list[EvalResultRecord]]:
        """Fetch eval results for multiple run IDs in one query.

        Returns a dict mapping run_id -> list of EvalResultRecord.
        """
        ...

    @abstractmethod
    def get_score_history(self, suite_name, limit=30) -> list[tuple[str, float]]:
        ...

    def get_score_history_batch(self, suite_names: list[str],
                                limit_per_suite: int = 30) -> dict[str, list[tuple[str, float]]]:
        """Score history for many suites at once. Default loops
        ``get_score_history``; SQLite overrides it with a single windowed query.
        Backends that can batch should override for the dashboard's suite list."""
        return {name: self.get_score_history(name, limit=limit_per_suite)
                for name in suite_names}

    @abstractmethod
    def get_runs_by_model(self, suite_name, model_version, limit=200) -> list[EvalRunRecord]:
        ...

    @abstractmethod
    def get_model_versions(self, suite_name) -> list[tuple[str, int]]:
        """Return (model_version, run_count) pairs for a suite, ordered by most runs."""
        ...

    @abstractmethod
    def list_suite_names(self) -> list[str]:
        ...

    @abstractmethod
    def get_eval_run_by_id(self, run_id: int) -> "EvalRunRecord | None":
        ...

    @abstractmethod
    def get_cost_data(self, days: int = 7, name: str | None = None, model: str | None = None) -> dict:
        ...

    @abstractmethod
    def get_model_cost_summary(self, model: str, days: int = 30) -> dict:
        """SQL-side cost/latency rollup for one model: {cost, calls, avg_latency}."""
        ...

    @abstractmethod
    def list_prompt_summaries(self, offset: int = 0, limit: int = 200) -> list[dict]:
        """One row per prompt name: {name, latest_version, tags}. For the
        registry list view (so heavily-versioned prompts don't hide others)."""
        ...

    @abstractmethod
    def list_latest_contents(self, limit: int = 500) -> list[tuple[str, str]]:
        """(name, content) for the latest version of every prompt name, in a
        single query. Used by prompt_search to avoid an N+1 of
        list_prompt_summaries() + get_prompt() per name."""
        ...

    @abstractmethod
    def get_invocation_stats(self, name: str, days: int = 30) -> dict:
        """Per-call distribution stats for a prompt from the invocations
        ledger (count, min/avg/p50/p95/max per metric, input-size histogram)."""
        ...

    # ---- invocations ----

    @abstractmethod
    def record_invocation(self, prompt_name: str, metadata: dict | None = None, prompt_version: int | None = None) -> int:
        """Append one row to the per-call ledger. No dedup. Returns row id."""
        ...

    # ---- datasets ----

    @abstractmethod
    def save_dataset(self, name: str, items: list, metadata=None) -> int:
        """Save a dataset of input/output pairs. Returns version number."""
        ...

    @abstractmethod
    def get_dataset(self, name: str, version: int | None = None) -> dict | None:
        """Get a dataset by name. Returns latest version if no version given."""
        ...

    @abstractmethod
    def list_datasets(self) -> list[dict]:
        """List all datasets with name, latest version, and item count."""
        ...

    # ---- votes ----

    @abstractmethod
    def save_vote(self, prompt_name, response, score, prompt_version=None, message=None, metadata=None) -> int:
        """Save a vote. Returns vote id."""
        ...

    @abstractmethod
    def get_votes(self, prompt_name=None, days=30, offset=0, limit=200) -> list[dict]:
        """Get recent votes. Returns list of vote dicts."""
        ...

    @abstractmethod
    def get_vote_stats(self, prompt_name=None, days=30) -> dict:
        """Aggregate vote stats per prompt name and version."""
        ...

    @abstractmethod
    def close(self):
        ...

    # ---- capability probing ----

    def supports(self, capability: str) -> bool:
        """Whether this backend actually implements `capability` (vs. just
        inheriting BaseStorage's NotImplementedError default)."""
        base_attr = getattr(BaseStorage, capability, None)
        if base_attr is None:
            return False
        return getattr(type(self), capability, None) is not base_attr

    # ---- prompt promotion / linkage (optional capability) ----

    def set_prompt_env(self, name: str, version: int, env: str) -> bool:
        """Point an environment tag at a specific prompt version. Optional."""
        raise NotImplementedError

    def get_runs_for_prompt(self, prompt_name: str, limit: int = 50) -> list[dict]:
        """Eval runs that exercised a given prompt, newest first. Optional."""
        raise NotImplementedError

    def prune_prompt_versions(self, name: str, keep_last: int = 1) -> int:
        """Delete all but the newest keep_last versions of a prompt. Optional."""
        raise NotImplementedError

    # ---- invocations ledger (optional capability) ----

    def list_invocations(self, name: str | None = None, days: int = 7, limit: int = 100,
                         offset: int = 0, captured_only: bool = False, order: str = "recent",
                         sort: str | None = None, direction: str = "desc",
                         min_rating: float | None = None) -> list[dict]:
        """Paged per-call invocations, optionally filtered/sorted. Optional."""
        raise NotImplementedError

    def get_invocation(self, invocation_id: int) -> dict | None:
        """Full invocation incl. captured text and feedback. Optional."""
        raise NotImplementedError

    def get_invocation_models(self, days: int = 30) -> list[dict]:
        """Distinct models seen in the invocations ledger with call counts. Optional."""
        raise NotImplementedError

    def count_invocations(self) -> int:
        """Total rows in the per-call ledger (all time). Optional."""
        raise NotImplementedError

    # ---- feedback (optional capability) ----

    def save_feedback(self, request_id: str, rating: float | None = None,
                      comment: str | None = None, source: str | None = None) -> int:
        """Store an end-user rating/comment for an invocation. Optional."""
        raise NotImplementedError

    def list_feedback(self, name: str | None = None, days: int = 30, limit: int = 50,
                      offset: int = 0, only_comments: bool = False,
                      min_rating: float | None = None, q: str | None = None) -> list[dict]:
        """End-user feedback rows, newest first. Optional."""
        raise NotImplementedError

    def get_feedback_stats(self, days: int = 30, positive_at: float = 0.7,
                           negative_at: float = 0.4) -> dict:
        """Aggregate feedback satisfaction/counts/breakdown over a window. Optional."""
        raise NotImplementedError

    # ---- eval bisection (optional capability) ----

    def bisect_regression(self, suite_name: str) -> dict:
        """Find the first eval run where a suite regressed from passing to
        failing. Returns {found, run, previous} or {found: False}. Optional."""
        raise NotImplementedError

    # ---- budgets (optional capability) ----

    def save_budget(self, scope: str, period: str, limit_usd: float, target: str | None = None) -> int:
        """Create a spend budget for a scope/period. Optional."""
        raise NotImplementedError

    def delete_budget(self, budget_id: int) -> None:
        """Delete a budget by id. Optional."""
        raise NotImplementedError

    def list_budgets(self) -> list[dict]:
        """List all budgets. Optional."""
        raise NotImplementedError

    def get_budget_status(self) -> list[dict]:
        """Each budget with its current-period spend, % used, and breach flag. Optional."""
        raise NotImplementedError

    # ---- golden examples (optional capability) ----

    def add_golden_example(self, prompt_name: str, input_text: str,
                           reference_output: str | None = None,
                           source_invocation_id: int | None = None,
                           model: str | None = None) -> int:
        """Promote a captured invocation into a golden example. Optional."""
        raise NotImplementedError

    def list_golden_examples(self, prompt_name: str) -> list[dict]:
        """Golden examples for a prompt, newest first. Optional."""
        raise NotImplementedError

    def delete_golden_example(self, example_id: int) -> bool:
        """Delete a golden example by id. Optional."""
        raise NotImplementedError

    # ---- users / multi-user identity (optional capability) ----

    def count_users(self) -> int:
        """Number of local user accounts (0 = single-token/open mode). Optional."""
        raise NotImplementedError

    def create_user(self, email: str, *, password_hash: str | None = None,
                    name: str | None = None, role: str = "viewer",
                    is_active: bool = True) -> dict:
        """Create a local user; password_hash is None for OIDC-only. Optional."""
        raise NotImplementedError

    def get_user_by_email(self, email: str) -> dict | None:
        """Look up a user by (case-folded) email. Optional."""
        raise NotImplementedError

    def get_user_by_id(self, user_id: int) -> dict | None:
        """Look up a user by id. Optional."""
        raise NotImplementedError

    def list_users(self) -> list[dict]:
        """All users (no password hashes). Optional."""
        raise NotImplementedError

    def update_user(self, user_id: int, *, role: str | None = None,
                    is_active: bool | None = None, name: str | None = None,
                    password_hash: str | None = None) -> bool:
        """Patch mutable user fields. Optional."""
        raise NotImplementedError

    def touch_user_login(self, user_id: int) -> None:
        """Stamp last_login_at = now. Optional."""
        raise NotImplementedError

    def delete_user(self, user_id: int) -> bool:
        """Delete a user (cascades linked identities). Optional."""
        raise NotImplementedError

    def link_identity(self, user_id: int, provider: str, subject: str) -> None:
        """Bind an external (OIDC) identity to a user. Optional."""
        raise NotImplementedError

    def get_user_by_identity(self, provider: str, subject: str) -> dict | None:
        """Resolve a user from an external identity. Optional."""
        raise NotImplementedError

    # ---- audit log (optional capability, append-only) ----

    def record_audit(self, action: str, *, actor: str | None = None,
                     actor_id: int | None = None, target: str | None = None,
                     ip: str | None = None, result: str = "ok",
                     detail: dict | None = None) -> int:
        """Append one immutable audit entry. Optional."""
        raise NotImplementedError

    def list_audit(self, *, limit: int = 100, offset: int = 0,
                   action: str | None = None, actor: str | None = None,
                   since: str | None = None) -> list[dict]:
        """Audit entries, newest first, optionally filtered. Optional."""
        raise NotImplementedError

    def count_audit(self, *, action: str | None = None, actor: str | None = None,
                    since: str | None = None) -> int:
        """Count audit entries matching the filter. Optional."""
        raise NotImplementedError

    # ---- data retention (optional capability) ----

    def redact_old_capture_text(self, days: int) -> int:
        """NULL out captured input/output text on invocations older than `days`,
        keeping the row and its metrics. Returns rows affected. Optional."""
        raise NotImplementedError

    def purge_old_invocations(self, days: int) -> int:
        """Delete invocation rows older than `days`. Returns rows deleted. Optional."""
        raise NotImplementedError

    def purge_old_audit(self, days: int) -> int:
        """Delete audit entries older than `days`. Returns rows deleted. Optional."""
        raise NotImplementedError

    # ---- cost-attributed call trees (optional capability) ----

    def get_trace(self, trace_id: str) -> list[dict]:
        """Invocations in one trace, oldest first, with per-step cost. Optional."""
        raise NotImplementedError

    def list_traces(self, days: int = 7, limit: int = 100) -> list[dict]:
        """Recent traces with step count + total cost/tokens. Optional."""
        raise NotImplementedError
