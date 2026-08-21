"""Remote telemetry storage backend.

Dual-write: all data goes to local SQLite (for reads/evals) AND gets
batched and shipped to a remote endpoint (for centralized collection).

Configure in promptry.toml:
    [storage]
    mode = "remote"
    endpoint = "https://your-server.com/ingest"
    api_key = "pk_..."

The batch payload shipped by _ship_batch is governed by the shared wire
contract at docs/wire-schema/events.schema.json. The JS client
(promptry-js) emits that exact same envelope to the same endpoint, so
both Python and JS telemetry land in the same SQLite + dashboard.
"""
from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from promptry.storage.base import BaseStorage
from promptry.storage.sqlite import SQLiteStorage
from promptry.models import PromptRecord

log = logging.getLogger(__name__)


@dataclass
class TelemetryEvent:
    """A single event to ship to the remote endpoint."""
    event_type: str
    data: dict[str, Any]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class RemoteStorage(BaseStorage):
    """Dual-write storage: local SQLite + remote HTTP endpoint.

    All reads go to local SQLite. All writes go to both local SQLite
    and a batched queue that ships events to the remote endpoint.

    If the remote endpoint is unreachable, events are retried with
    backoff. Local storage always works regardless of network state.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        batch_size: int = 10,
        flush_interval: float = 5.0,
        max_queue: int = 10000,
        max_retries: int = 3,
    ):
        self._local = SQLiteStorage()
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._queue: queue.Queue[TelemetryEvent] = queue.Queue(maxsize=max_queue)
        self._running = True
        self._thread = threading.Thread(
            target=self._flush_loop,
            name="promptry-remote",
            daemon=True,
        )
        self._thread.start()
        atexit.register(self.flush)

    def _flush_loop(self):
        """Batch events and ship them periodically."""
        while self._running:
            batch: list[TelemetryEvent] = []
            deadline = time.monotonic() + self._flush_interval

            while len(batch) < self._batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    event = self._queue.get(timeout=min(remaining, 0.5))
                    batch.append(event)
                    self._queue.task_done()
                except queue.Empty:
                    continue

            if batch:
                self._ship_batch(batch)

        # drain remaining on shutdown
        batch = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
                self._queue.task_done()
            except queue.Empty:
                break
        if batch:
            self._ship_batch(batch)

    def _ship_batch(self, batch: list[TelemetryEvent]):
        """POST a batch of events to the remote endpoint."""
        payload = json.dumps({
            "events": [
                {"type": e.event_type, "data": e.data, "timestamp": e.timestamp}
                for e in batch
            ]
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        for attempt in range(self._max_retries):
            try:
                req = Request(
                    self._endpoint,
                    data=payload,
                    headers=headers,
                    method="POST",
                )
                with urlopen(req, timeout=10) as resp:
                    if resp.status < 300:
                        return
                    log.warning("remote ingest returned %d", resp.status)
            except (URLError, OSError) as exc:
                if attempt < self._max_retries - 1:
                    time.sleep(min(2 ** attempt, 10))
                else:
                    log.warning(
                        "failed to ship %d events after %d retries: %s",
                        len(batch), self._max_retries, exc,
                    )

    def _emit(self, event_type: str, data: dict):
        """Queue a telemetry event for remote shipping."""
        try:
            self._queue.put_nowait(TelemetryEvent(event_type=event_type, data=data))
        except queue.Full:
            log.warning("telemetry queue full, dropping %s event", event_type)

    def flush(self, timeout: float = 10.0):
        """Wait for all queued events to be shipped."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("flush timed out with %d events pending", self._queue.qsize())
                return
            time.sleep(min(0.05, remaining))

    @property
    def pending(self) -> int:
        """Number of events waiting to be shipped."""
        return self._queue.qsize()

    # ---- write methods (dual-write: local + remote) ----

    def save_prompt(self, name, content, content_hash, metadata=None) -> PromptRecord:
        record = self._local.save_prompt(name, content, content_hash, metadata)
        self._emit("prompt_save", {
            "name": record.name,
            "version": record.version,
            "content": record.content,
            "hash": record.hash,
            "metadata": record.metadata,
            "created_at": record.created_at,
        })
        return record

    def save_eval_run(
        self, suite_name, prompt_name=None, prompt_version=None,
        model_version=None, overall_pass=True, overall_score=None,
    ) -> int:
        run_id = self._local.save_eval_run(
            suite_name, prompt_name, prompt_version,
            model_version, overall_pass, overall_score,
        )
        self._emit("eval_run", {
            "run_id": run_id,
            "suite_name": suite_name,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "model_version": model_version,
            "overall_pass": overall_pass,
            "overall_score": overall_score,
        })
        return run_id

    def save_eval_result(
        self, run_id, test_name, assertion_type, passed,
        score=None, details=None, latency_ms=None,
    ) -> int:
        result_id = self._local.save_eval_result(
            run_id, test_name, assertion_type, passed,
            score, details, latency_ms,
        )
        self._emit("eval_result", {
            "run_id": run_id,
            "test_name": test_name,
            "assertion_type": assertion_type,
            "passed": passed,
            "score": score,
            "details": details,
            "latency_ms": latency_ms,
        })
        return result_id

    def save_eval_run_atomic(
        self, *, results, suite_name, prompt_name=None, prompt_version=None,
        model_version=None, overall_pass=True, overall_score=None,
    ) -> int:
        # The local SQLite store is the atomic source of truth (run + all
        # results in one transaction). Remote telemetry is shipped per event,
        # best-effort, exactly as save_eval_run/save_eval_result do.
        run_id = self._local.save_eval_run_atomic(
            results=results, suite_name=suite_name, prompt_name=prompt_name,
            prompt_version=prompt_version, model_version=model_version,
            overall_pass=overall_pass, overall_score=overall_score,
        )
        self._emit("eval_run", {
            "run_id": run_id,
            "suite_name": suite_name,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "model_version": model_version,
            "overall_pass": overall_pass,
            "overall_score": overall_score,
        })
        for r in results:
            self._emit("eval_result", {
                "run_id": run_id,
                "test_name": r["test_name"],
                "assertion_type": r["assertion_type"],
                "passed": r["passed"],
                "score": r.get("score"),
                "details": r.get("details"),
                "latency_ms": r.get("latency_ms"),
            })
        return run_id

    def tag_prompt(self, prompt_id, tag):
        self._local.tag_prompt(prompt_id, tag)
        self._emit("prompt_tag", {"prompt_id": prompt_id, "tag": tag})

    def save_dataset(self, name, items, metadata=None) -> int:
        version = self._local.save_dataset(name, items, metadata)
        self._emit("dataset_save", {
            "name": name,
            "version": version,
            "item_count": len(items),
            "metadata": metadata,
        })
        return version

    def get_dataset(self, name, version=None):
        return self._local.get_dataset(name, version)

    def list_datasets(self):
        return self._local.list_datasets()

    def save_vote(self, prompt_name, response, score, prompt_version=None, message=None, metadata=None) -> int:
        vote_id = self._local.save_vote(prompt_name, response, score, prompt_version, message, metadata)
        self._emit("vote", {
            "vote_id": vote_id,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "response": response,
            "score": score,
            "message": message,
            "metadata": metadata,
        })
        return vote_id

    # ---- read methods (local SQLite passthrough) ----

    def get_prompt(self, name, version=None):
        return self._local.get_prompt(name, version)

    def get_prompt_by_tag(self, name, tag):
        return self._local.get_prompt_by_tag(name, tag)

    def list_prompts(self, name=None, offset=0, limit=100):
        return self._local.list_prompts(name, offset=offset, limit=limit)

    def get_tags(self, prompt_id):
        return self._local.get_tags(prompt_id)

    def get_eval_runs(self, suite_name, offset=0, limit=50):
        return self._local.get_eval_runs(suite_name, offset=offset, limit=limit)

    def get_eval_runs_batch(self, suite_names, limit_per_suite=20):
        return self._local.get_eval_runs_batch(suite_names, limit_per_suite)

    def get_eval_results(self, run_id):
        return self._local.get_eval_results(run_id)

    def get_eval_results_batch(self, run_ids):
        return self._local.get_eval_results_batch(run_ids)

    def get_score_history(self, suite_name, limit=30):
        return self._local.get_score_history(suite_name, limit)

    def get_score_history_batch(self, suite_names, limit_per_suite=30):
        return self._local.get_score_history_batch(suite_names, limit_per_suite)

    def get_runs_by_model(self, suite_name, model_version, limit=200):
        return self._local.get_runs_by_model(suite_name, model_version, limit)

    def get_model_versions(self, suite_name):
        return self._local.get_model_versions(suite_name)

    def list_suite_names(self) -> list[str]:
        return self._local.list_suite_names()

    def get_eval_run_by_id(self, run_id: int):
        return self._local.get_eval_run_by_id(run_id)

    def get_cost_data(self, days: int = 7, name=None, model=None) -> dict:
        return self._local.get_cost_data(days, name, model)

    def get_model_cost_summary(self, model: str, days: int = 30) -> dict:
        return self._local.get_model_cost_summary(model, days=days)

    def list_prompt_summaries(self, offset: int = 0, limit: int = 200):
        return self._local.list_prompt_summaries(offset=offset, limit=limit)

    def list_latest_contents(self, limit: int = 500):
        return self._local.list_latest_contents(limit=limit)

    def prune_prompt_versions(self, name: str, keep_last: int = 1) -> int:
        return self._local.prune_prompt_versions(name, keep_last=keep_last)

    def get_invocation_stats(self, name: str, days: int = 30) -> dict:
        return self._local.get_invocation_stats(name, days=days)

    def record_invocation(self, prompt_name: str, metadata=None, prompt_version=None, **kwargs) -> int:
        # Record to the local store; remote shipping of invocations isn't a
        # distinct event type, so the local ledger is the source of truth.
        return self._local.record_invocation(
            prompt_name, metadata=metadata, prompt_version=prompt_version, **kwargs
        )

    def get_votes(self, prompt_name=None, days=30, offset=0, limit=200):
        return self._local.get_votes(prompt_name, days, offset=offset, limit=limit)

    def get_vote_stats(self, prompt_name=None, days=30):
        return self._local.get_vote_stats(prompt_name, days)

    # ---- optional capabilities (local SQLite passthrough) ----
    #
    # These aren't part of the remote wire schema (docs/wire-schema/events.schema.json)
    # yet, so they aren't shipped as telemetry events -- same rationale as
    # record_invocation above. The local SQLite store is the source of truth
    # for them, and RemoteStorage always keeps one, so proxy straight through
    # rather than silently pretending the capability doesn't exist.

    def set_prompt_env(self, name, version, env):
        return self._local.set_prompt_env(name, version, env)

    def get_runs_for_prompt(self, prompt_name, limit=50):
        return self._local.get_runs_for_prompt(prompt_name, limit=limit)

    def list_invocations(self, name=None, days=7, limit=100, offset=0,
                         captured_only=False, order="recent", sort=None,
                         direction="desc", min_rating=None):
        return self._local.list_invocations(
            name=name, days=days, limit=limit, offset=offset,
            captured_only=captured_only, order=order, sort=sort,
            direction=direction, min_rating=min_rating,
        )

    def get_invocation(self, invocation_id):
        return self._local.get_invocation(invocation_id)

    def get_invocation_models(self, days=30):
        return self._local.get_invocation_models(days=days)

    def count_invocations(self) -> int:
        return self._local.count_invocations()

    def save_feedback(self, request_id, rating=None, comment=None, source=None):
        return self._local.save_feedback(request_id, rating=rating, comment=comment, source=source)

    def list_feedback(self, name=None, days=30, limit=50, offset=0,
                      only_comments=False, min_rating=None, q=None):
        return self._local.list_feedback(
            name=name, days=days, limit=limit, offset=offset,
            only_comments=only_comments, min_rating=min_rating, q=q,
        )

    def get_feedback_stats(self, days=30, positive_at=0.7, negative_at=0.4):
        return self._local.get_feedback_stats(days=days, positive_at=positive_at, negative_at=negative_at)

    def bisect_regression(self, suite_name):
        return self._local.bisect_regression(suite_name)

    def save_budget(self, scope, period, limit_usd, target=None) -> int:
        return self._local.save_budget(scope, period, limit_usd, target=target)

    def delete_budget(self, budget_id) -> None:
        return self._local.delete_budget(budget_id)

    def list_budgets(self):
        return self._local.list_budgets()

    def get_budget_status(self):
        return self._local.get_budget_status()

    def add_golden_example(self, prompt_name, input_text, reference_output=None,
                           source_invocation_id=None, model=None) -> int:
        return self._local.add_golden_example(
            prompt_name, input_text, reference_output=reference_output,
            source_invocation_id=source_invocation_id, model=model,
        )

    def list_golden_examples(self, prompt_name):
        return self._local.list_golden_examples(prompt_name)

    def delete_golden_example(self, example_id) -> bool:
        return self._local.delete_golden_example(example_id)

    # ---- users / identity / audit (local-only; never shipped as telemetry) ----
    # User accounts and the audit trail are deployment-local by design, so these
    # proxy straight to the local SQLite store without emitting remote events.

    def count_users(self) -> int:
        return self._local.count_users()

    def create_user(self, email, *, password_hash=None, name=None,
                    role="viewer", is_active=True) -> dict:
        return self._local.create_user(
            email, password_hash=password_hash, name=name,
            role=role, is_active=is_active,
        )

    def get_user_by_email(self, email):
        return self._local.get_user_by_email(email)

    def get_user_by_id(self, user_id):
        return self._local.get_user_by_id(user_id)

    def list_users(self) -> list:
        return self._local.list_users()

    def update_user(self, user_id, *, role=None, is_active=None,
                    name=None, password_hash=None) -> bool:
        return self._local.update_user(
            user_id, role=role, is_active=is_active,
            name=name, password_hash=password_hash,
        )

    def touch_user_login(self, user_id) -> None:
        self._local.touch_user_login(user_id)

    def delete_user(self, user_id) -> bool:
        return self._local.delete_user(user_id)

    def link_identity(self, user_id, provider, subject) -> None:
        self._local.link_identity(user_id, provider, subject)

    def get_user_by_identity(self, provider, subject):
        return self._local.get_user_by_identity(provider, subject)

    def record_audit(self, action, *, actor=None, actor_id=None, target=None,
                     ip=None, result="ok", detail=None) -> int:
        return self._local.record_audit(
            action, actor=actor, actor_id=actor_id, target=target,
            ip=ip, result=result, detail=detail,
        )

    def list_audit(self, *, limit=100, offset=0, action=None, actor=None, since=None) -> list:
        return self._local.list_audit(
            limit=limit, offset=offset, action=action, actor=actor, since=since,
        )

    def count_audit(self, *, action=None, actor=None, since=None) -> int:
        return self._local.count_audit(action=action, actor=actor, since=since)

    def redact_old_capture_text(self, days) -> int:
        return self._local.redact_old_capture_text(days)

    def purge_old_invocations(self, days) -> int:
        return self._local.purge_old_invocations(days)

    def purge_old_audit(self, days) -> int:
        return self._local.purge_old_audit(days)

    def get_trace(self, trace_id) -> list:
        return self._local.get_trace(trace_id)

    def list_traces(self, days: int = 7, limit: int = 100) -> list:
        return self._local.list_traces(days, limit)

    def close(self):
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=self._flush_interval + 2)
        self._local.close()
