"""Production capture + CI replay (v0.9 C1).

The idea: in production, you wrap LLM pipeline calls and record the
(input, output, metadata) triples to an append-only JSONL file on disk.
In CI or dev, you replay those captured inputs through your CURRENT
pipeline and compare — usually with a semantic assertion — against the
captured output. This lets you ask:

    "If I change the prompt from v7 to v8, does it regress on real
     production queries?" — not on a synthetic eval set.

The capture file is plain JSONL, append-only, one entry per line. It
never leaves the user's disk. No network, no provider, no daemon.

Minimal shape of a capture entry::

    {
      "ts": "2026-04-18T03:14:07Z",
      "task": "rag-qa",
      "input": <serializable>,
      "output": <serializable>,
      "metadata": {"model": "...", "prompt_version": 4, ...},
      "duration_ms": 842.1,
      "tokens_in": 120,
      "tokens_out": 55
    }
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


DEFAULT_CAPTURE_DIR = ".promptry/captures"


# Per-path locks shared across all CaptureRecorder instances. Two
# recorders writing to the same file must serialize — otherwise we
# race on file.open/write and lose lines.
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


@dataclass
class Capture:
    """One recorded production invocation."""
    ts: str
    task: str
    input: Any
    output: Any
    metadata: dict = field(default_factory=dict)
    duration_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "ts": self.ts,
            "task": self.task,
            "input": self.input,
            "output": self.output,
            "metadata": self.metadata,
            "duration_ms": self.duration_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, line: str) -> "Capture":
        d = json.loads(line)
        return cls(
            ts=d.get("ts", ""),
            task=d.get("task", ""),
            input=d.get("input"),
            output=d.get("output"),
            metadata=d.get("metadata") or {},
            duration_ms=float(d.get("duration_ms") or 0.0),
            tokens_in=int(d.get("tokens_in") or 0),
            tokens_out=int(d.get("tokens_out") or 0),
        )


# ---------------------------------------------------------------------------
# Writer (capture-side)
# ---------------------------------------------------------------------------

class CaptureRecorder:
    """Append-only JSONL capture file. Thread-safe."""

    def __init__(
        self,
        path: str | Path,
        task: str,
        sample_rate: float = 1.0,
        scrub: Callable[[dict], dict] | None = None,
    ):
        self.path = Path(path)
        self.task = task
        self.sample_rate = max(0.0, min(1.0, float(sample_rate)))
        self.scrub = scrub
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Per-path lock so multiple CaptureRecorder instances targeting
        # the same file don't race and lose lines.
        self._lock = _lock_for(self.path)

    def write(
        self,
        input: Any,
        output: Any,
        *,
        metadata: dict | None = None,
        duration_ms: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> bool:
        """Record one capture. Returns True if written, False if sampled out."""
        if self.sample_rate < 1.0 and random.random() > self.sample_rate:
            return False

        entry = Capture(
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            task=self.task,
            input=input,
            output=output,
            metadata=dict(metadata or {}),
            duration_ms=float(duration_ms),
            tokens_in=int(tokens_in),
            tokens_out=int(tokens_out),
        )
        if self.scrub:
            try:
                scrubbed = self.scrub(entry.metadata)
                if isinstance(scrubbed, dict):
                    entry.metadata = scrubbed
            except Exception:
                # never let scrubbing break recording
                pass

        line = entry.to_json() + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line)
        return True

    @contextmanager
    def record(
        self,
        input: Any,
        *,
        metadata: dict | None = None,
    ):
        """Time-and-record context manager. Yields a dict you can mutate
        to attach tokens/metadata before the capture is written.

        Usage::

            with recorder.record(input=user_q, metadata={"model": "..."}) as ctx:
                result = pipeline(user_q)
                ctx["output"] = result
                ctx["tokens_in"] = 120
                ctx["tokens_out"] = 55
        """
        ctx: dict = {
            "output": None,
            "metadata": dict(metadata or {}),
            "tokens_in": 0,
            "tokens_out": 0,
        }
        t0 = time.perf_counter()
        try:
            yield ctx
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            self.write(
                input=input,
                output=ctx.get("output"),
                metadata=ctx.get("metadata") or {},
                duration_ms=duration_ms,
                tokens_in=int(ctx.get("tokens_in") or 0),
                tokens_out=int(ctx.get("tokens_out") or 0),
            )


def get_recorder(
    task: str,
    *,
    dir: str | Path = DEFAULT_CAPTURE_DIR,
    sample_rate: float = 1.0,
    scrub: Callable[[dict], dict] | None = None,
) -> CaptureRecorder:
    """Convenience: build a recorder writing to `<dir>/<task>.jsonl`."""
    path = Path(dir) / f"{task}.jsonl"
    return CaptureRecorder(path=path, task=task, sample_rate=sample_rate, scrub=scrub)


# ---------------------------------------------------------------------------
# Reader (replay-side)
# ---------------------------------------------------------------------------

def load_captures(
    path: str | Path,
    *,
    limit: int | None = None,
    task: str | None = None,
) -> list[Capture]:
    """Load captures from a JSONL file.

    Malformed lines are skipped silently (they won't fail the whole replay).
    `limit` applies AFTER task filtering so you always get up to N matching.
    """
    p = Path(path)
    if not p.is_file():
        return []

    out: list[Capture] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cap = Capture.from_json(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if task is not None and cap.task != task:
                continue
            out.append(cap)
            if limit is not None and len(out) >= limit:
                break
    return out


def iter_captures(
    path: str | Path,
    *,
    task: str | None = None,
) -> Iterable[Capture]:
    """Stream captures without loading the whole file into memory."""
    p = Path(path)
    if not p.is_file():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cap = Capture.from_json(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if task is not None and cap.task != task:
                continue
            yield cap


# ---------------------------------------------------------------------------
# Replay helpers
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    captures: int
    matched: int
    drifted: int
    errors: int
    examples_drifted: list[dict] = field(default_factory=list)


def replay_captures(
    captures: list[Capture] | Iterable[Capture],
    pipeline: Callable[[Any], Any],
    *,
    compare: Callable[[Any, Any], bool] | None = None,
    max_examples: int = 5,
    concurrency: int = 8,
) -> ReplayResult:
    """Replay captured inputs through `pipeline`, compare to captured output.

    - `pipeline(input)` returns the candidate output.
    - `compare(candidate, baseline)` returns True if they agree. Defaults to
      exact equality. Pass a fuzzy matcher (e.g., semantic-similarity over
      0.8) for real-world replay.
    - `max_examples` bounds the number of drifted captures kept in the
      result (so we don't bloat memory on huge replay files).
    - `concurrency` controls how many pipeline calls run in parallel via a
      `ThreadPoolExecutor` (I/O-bound work). Results are always mapped back
      to input order regardless of completion order. A per-item pipeline
      exception is captured as a replay error rather than aborting the
      batch. `concurrency=1` runs strictly serially with identical
      semantics to a plain for-loop.
    """
    if compare is None:
        compare = _default_compare

    caps_list = list(captures)

    def _invoke(cap: Capture) -> tuple[Any, Exception | None]:
        try:
            return pipeline(cap.input), None
        except Exception as e:
            return None, e

    if concurrency == 1:
        outcomes = [_invoke(cap) for cap in caps_list]
    else:
        outcomes: list[tuple[Any, Exception | None]] = [None] * len(caps_list)  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_invoke, cap) for cap in caps_list]
            for i, fut in enumerate(futures):
                outcomes[i] = fut.result()

    total = 0
    matched = 0
    drifted = 0
    errors = 0
    examples: list[dict] = []

    for cap, (candidate, exc) in zip(caps_list, outcomes):
        total += 1
        if exc is not None:
            errors += 1
            if len(examples) < max_examples:
                examples.append({
                    "ts": cap.ts,
                    "task": cap.task,
                    "input": cap.input,
                    "expected": cap.output,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            continue

        if compare(candidate, cap.output):
            matched += 1
        else:
            drifted += 1
            if len(examples) < max_examples:
                examples.append({
                    "ts": cap.ts,
                    "task": cap.task,
                    "input": cap.input,
                    "expected": cap.output,
                    "got": candidate,
                })

    return ReplayResult(
        captures=total,
        matched=matched,
        drifted=drifted,
        errors=errors,
        examples_drifted=examples,
    )


def _default_compare(a: Any, b: Any) -> bool:
    """Strict equality default. Meant to be overridden with a fuzzy matcher."""
    return a == b


# ---------------------------------------------------------------------------
# Scrubbing helpers (common redactions for metadata at capture time)
# ---------------------------------------------------------------------------

_DEFAULT_SCRUB_KEYS = frozenset({
    "api_key", "apikey", "password", "token", "bearer",
    "authorization", "secret", "cookie",
})


def redact_sensitive(meta: dict, extra_keys: set[str] | None = None) -> dict:
    """Return a copy of `meta` with common-sensitive keys redacted.

    Case-insensitive key match. Values become '***redacted***'. Nested dicts
    are walked recursively. Default keys cover auth bearer tokens, API keys,
    cookies, passwords. Pass extra_keys to cover domain-specific fields.
    """
    keys = set(_DEFAULT_SCRUB_KEYS)
    if extra_keys:
        keys.update(k.lower() for k in extra_keys)

    def _walk(v):
        if isinstance(v, dict):
            return {
                k: ("***redacted***" if k.lower() in keys else _walk(val))
                for k, val in v.items()
            }
        if isinstance(v, list):
            return [_walk(x) for x in v]
        return v

    return _walk(meta)


# ---------------------------------------------------------------------------
# Env-var controlled singleton (for "drop-in prod capture without refactor")
# ---------------------------------------------------------------------------

_singleton_lock = threading.Lock()
_singletons: dict[str, CaptureRecorder] = {}


def capture_enabled() -> bool:
    """Shortcut for `os.environ.get("PROMPTRY_CAPTURE")` being truthy."""
    return bool(os.environ.get("PROMPTRY_CAPTURE", "").strip())


def default_recorder(task: str) -> CaptureRecorder | None:
    """Return a process-wide recorder for `task` only if PROMPTRY_CAPTURE is set.

    Honors PROMPTRY_CAPTURE_DIR (path), PROMPTRY_CAPTURE_SAMPLE (float 0-1).
    Returns None when capture is disabled, so callers can guard cheaply::

        rec = default_recorder("rag-qa")
        if rec:
            with rec.record(input=q) as ctx:
                ctx["output"] = pipeline(q)
        else:
            pipeline(q)
    """
    if not capture_enabled():
        return None
    with _singleton_lock:
        if task in _singletons:
            return _singletons[task]
        cap_dir = os.environ.get("PROMPTRY_CAPTURE_DIR", DEFAULT_CAPTURE_DIR)
        try:
            sample = float(os.environ.get("PROMPTRY_CAPTURE_SAMPLE", "1.0"))
        except ValueError:
            sample = 1.0
        rec = get_recorder(task, dir=cap_dir, sample_rate=sample)
        _singletons[task] = rec
        return rec


def reset_recorders() -> None:
    """Drop the singleton cache. Useful in tests."""
    with _singleton_lock:
        _singletons.clear()
