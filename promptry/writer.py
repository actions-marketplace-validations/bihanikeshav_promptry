"""Async write queue for production use.

Wraps any BaseStorage with a background thread that drains writes.
The calling code (track(), track_context()) never blocks on I/O.

Three modes:
  - sync:  writes happen inline (default, good for dev/testing)
  - async: writes are queued and flushed by a daemon thread
  - off:   no writes at all, track() is pure passthrough
"""
from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from promptry.storage.base import BaseStorage

log = logging.getLogger(__name__)


@dataclass
class WriteOp:
    """A queued write operation."""
    method: str
    args: tuple
    kwargs: dict[str, Any]


class AsyncWriter(BaseStorage):
    """Wraps a storage backend with a background write queue.

    All read methods go straight to the underlying storage.
    Write methods (save_eval_run, save_eval_result, tag_prompt) get queued
    and processed by a single background thread.

    save_prompt is synchronous even in async mode because callers need the
    returned PromptRecord (version number, id). The dedup check makes it
    fast anyway (no write on duplicate content).
    """

    def __init__(self, storage: BaseStorage, max_queue: int = 10000):
        self._storage = storage
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(
            target=self._drain,
            name="promptry-writer",
            daemon=True,
        )
        self._thread.start()
        atexit.register(self.flush)

    def __getattr__(self, name):
        # Read methods added to the storage backend don't need an explicit
        # passthrough here — delegate any unknown attribute to the wrapped
        # storage. (Write methods that must be queued are defined explicitly
        # below; __getattr__ only fires for names not found on the instance.)
        # Guard against recursion before _storage is set.
        if name == "_storage":
            raise AttributeError(name)
        return getattr(self._storage, name)

    def _drain(self):
        """Process writes until stopped."""
        while self._running or not self._queue.empty():
            try:
                op = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                method = getattr(self._storage, op.method)
                method(*op.args, **op.kwargs)
            except Exception:
                log.exception("async write failed: %s", op.method)
            finally:
                self._queue.task_done()

    def _enqueue(self, method: str, *args, **kwargs):
        try:
            self._queue.put(WriteOp(method, args, kwargs), timeout=1.0)
        except queue.Full:
            log.warning(
                "write queue full, dropping %s — increase max_queue or check for slowdowns",
                method,
            )

    def flush(self, timeout: float = 5.0):
        """Wait for all pending writes to finish, with timeout."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("flush timed out with %d writes pending", self._queue.qsize())
                return
            time.sleep(min(0.05, remaining))

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ---- write methods ----

    def save_prompt(self, name, content, content_hash, metadata=None, force=False):
        # synchronous -- callers need the returned PromptRecord
        return self._storage.save_prompt(name, content, content_hash, metadata, force=force)

    def save_eval_run(self, **kwargs):
        # synchronous -- callers need the returned run_id
        return self._storage.save_eval_run(**kwargs)

    def save_eval_result(self, **kwargs):
        self._enqueue("save_eval_result", **kwargs)

    def tag_prompt(self, prompt_id, tag):
        self._enqueue("tag_prompt", prompt_id, tag)

    def save_vote(self, prompt_name, response, score, prompt_version=None, message=None, metadata=None) -> int:
        # synchronous -- callers need the returned vote_id
        return self._storage.save_vote(prompt_name, response, score, prompt_version, message, metadata)

    def record_invocation(self, prompt_name, metadata=None, prompt_version=None,
                          input_text=None, output_text=None, request_id=None) -> int:
        # Append-only ledger row. Run via the async queue so per-call
        # tracking doesn't block the LLM caller. The caller doesn't need
        # the row id back -- this is fire-and-forget telemetry.
        self._enqueue("record_invocation",
                      prompt_name=prompt_name,
                      metadata=metadata,
                      prompt_version=prompt_version,
                      input_text=input_text,
                      output_text=output_text,
                      request_id=request_id)
        return 0

    def save_dataset(self, name, items, metadata=None) -> int:
        # synchronous -- callers need the returned version number
        return self._storage.save_dataset(name, items, metadata)

    def get_dataset(self, name, version=None):
        return self._storage.get_dataset(name, version)

    def list_datasets(self):
        return self._storage.list_datasets()

    # ---- read methods (direct passthrough) ----

    def get_prompt(self, name, version=None):
        return self._storage.get_prompt(name, version)

    def get_prompt_by_tag(self, name, tag):
        return self._storage.get_prompt_by_tag(name, tag)

    def list_prompts(self, name=None, offset=0, limit=100):
        return self._storage.list_prompts(name, offset=offset, limit=limit)

    def list_prompt_summaries(self, offset=0, limit=200):
        return self._storage.list_prompt_summaries(offset=offset, limit=limit)

    def list_latest_contents(self, limit=500):
        return self._storage.list_latest_contents(limit=limit)

    def prune_prompt_versions(self, name, keep_last=1):
        return self._storage.prune_prompt_versions(name, keep_last=keep_last)

    def get_invocation_stats(self, name, days=30):
        return self._storage.get_invocation_stats(name, days=days)

    def get_tags(self, prompt_id):
        return self._storage.get_tags(prompt_id)

    def get_eval_runs(self, suite_name, offset=0, limit=50):
        return self._storage.get_eval_runs(suite_name, offset=offset, limit=limit)

    def get_eval_runs_batch(self, suite_names, limit_per_suite=20):
        return self._storage.get_eval_runs_batch(suite_names, limit_per_suite)

    def get_eval_results(self, run_id):
        return self._storage.get_eval_results(run_id)

    def get_eval_results_batch(self, run_ids):
        return self._storage.get_eval_results_batch(run_ids)

    def get_score_history(self, suite_name, limit=30):
        return self._storage.get_score_history(suite_name, limit)

    def get_runs_by_model(self, suite_name, model_version, limit=200):
        return self._storage.get_runs_by_model(suite_name, model_version, limit)

    def get_model_versions(self, suite_name):
        return self._storage.get_model_versions(suite_name)

    def list_suite_names(self) -> list[str]:
        return self._storage.list_suite_names()

    def get_eval_run_by_id(self, run_id: int):
        return self._storage.get_eval_run_by_id(run_id)

    def get_cost_data(self, days: int = 7, name=None, model=None) -> dict:
        return self._storage.get_cost_data(days, name, model)

    def get_model_cost_summary(self, model: str, days: int = 30) -> dict:
        return self._storage.get_model_cost_summary(model, days=days)

    def get_votes(self, prompt_name=None, days=30, offset=0, limit=200):
        return self._storage.get_votes(prompt_name, days, offset=offset, limit=limit)

    def get_vote_stats(self, prompt_name=None, days=30):
        return self._storage.get_vote_stats(prompt_name, days)

    # ---- optional capabilities (direct passthrough) ----
    #
    # BaseStorage now declares these with NotImplementedError defaults, so
    # they resolve on this class via normal MRO lookup instead of falling
    # through to __getattr__ above. Proxy explicitly so AsyncWriter honestly
    # reports (via supports()) whatever the wrapped storage supports.

    def supports(self, capability: str) -> bool:
        return self._storage.supports(capability)

    def set_prompt_env(self, name, version, env):
        return self._storage.set_prompt_env(name, version, env)

    def get_runs_for_prompt(self, prompt_name, limit=50):
        return self._storage.get_runs_for_prompt(prompt_name, limit=limit)

    def list_invocations(self, name=None, days=7, limit=100, offset=0,
                         captured_only=False, order="recent", sort=None,
                         direction="desc", min_rating=None):
        return self._storage.list_invocations(
            name=name, days=days, limit=limit, offset=offset,
            captured_only=captured_only, order=order, sort=sort,
            direction=direction, min_rating=min_rating,
        )

    def get_invocation(self, invocation_id):
        return self._storage.get_invocation(invocation_id)

    def get_invocation_models(self, days=30):
        return self._storage.get_invocation_models(days=days)

    def count_invocations(self) -> int:
        return self._storage.count_invocations()

    def save_feedback(self, request_id, rating=None, comment=None, source=None):
        return self._storage.save_feedback(request_id, rating=rating, comment=comment, source=source)

    def list_feedback(self, name=None, days=30, limit=50, offset=0,
                      only_comments=False, min_rating=None, q=None):
        return self._storage.list_feedback(
            name=name, days=days, limit=limit, offset=offset,
            only_comments=only_comments, min_rating=min_rating, q=q,
        )

    def get_feedback_stats(self, days=30, positive_at=0.7, negative_at=0.4):
        return self._storage.get_feedback_stats(days=days, positive_at=positive_at, negative_at=negative_at)

    def bisect_regression(self, suite_name):
        return self._storage.bisect_regression(suite_name)

    def save_budget(self, scope, period, limit_usd, target=None) -> int:
        return self._storage.save_budget(scope, period, limit_usd, target=target)

    def delete_budget(self, budget_id) -> None:
        return self._storage.delete_budget(budget_id)

    def list_budgets(self):
        return self._storage.list_budgets()

    def get_budget_status(self):
        return self._storage.get_budget_status()

    def add_golden_example(self, prompt_name, input_text, reference_output=None,
                           source_invocation_id=None, model=None) -> int:
        return self._storage.add_golden_example(
            prompt_name, input_text, reference_output=reference_output,
            source_invocation_id=source_invocation_id, model=model,
        )

    def list_golden_examples(self, prompt_name):
        return self._storage.list_golden_examples(prompt_name)

    def delete_golden_example(self, example_id) -> bool:
        return self._storage.delete_golden_example(example_id)

    def close(self):
        self._running = False
        self.flush()
        self._thread.join(timeout=2.0)
        self._storage.close()
