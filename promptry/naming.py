"""Zero-config prompt naming + cross-layer capture dedup.

A promptry prompt *name* is a stable identity across versions (name -> v1, v2,
v3 as the content changes). Auto-instrumentation shouldn't force the user to
invent one, so we infer it — but stably, so editing a prompt doesn't fork its
identity.

Precedence ladder (first hit wins), per the Fable design:
  1. an explicit name (e.g. OpenAI(promptry_task="checkout"))
  2. an ambient override:  with promptry.naming.task("checkout"): ...
  3. the call site -> "<module>:<qualname>"  (stable across content edits)
  4. a module-level call -> the module name
  5. a REPL / notebook -> "repl"
  6. anything else, or any error during the walk -> "unnamed"  (never raises)

We also expose a suppression ContextVar so that when several capture layers are
active at once (e.g. the LiteLLM callback AND the OpenAI drop-in, where LiteLLM
calls the OpenAI SDK), the inner layer can be told to stay quiet and let the
outer, provider-normalizing layer record the single logical call.
"""
from __future__ import annotations

import contextvars
import sys
import threading
from contextlib import contextmanager
from typing import Optional

# ---- ambient overrides / suppression ----

_task_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "promptry_task", default=None)
_suppress_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "promptry_suppress", default=False)
# (trace_id, span_name) for cost-attributed call trees — groups the LLM calls
# made inside a `with promptry.trace(...)` block so the dashboard can show a
# per-step token/$ waterfall ("which step of my agent burned the tokens").
_trace_var: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "promptry_trace", default=None)


@contextmanager
def task(name: str):
    """Name every tracked call in this block, regardless of call site. The
    escape hatch for a shared ``call_llm()`` helper where the call site is
    generic."""
    token = _task_var.set(name)
    try:
        yield
    finally:
        _task_var.reset(token)


def suppress_capture():
    """Mark the current context so inner drop-ins skip recording (an outer
    layer owns this call). Returns a token; pass it to ``resume_capture``."""
    return _suppress_var.set(True)


def resume_capture(token) -> None:
    try:
        _suppress_var.reset(token)
    except (ValueError, LookupError):
        pass


def is_suppressed() -> bool:
    return _suppress_var.get()


@contextmanager
def trace(name: str):
    """Group the LLM calls made inside this block into one cost-attributed call
    tree. Nested traces share the outermost trace id; the innermost name labels
    the step. Yields the trace id.

        with promptry.trace("checkout_agent"):
            client.chat.completions.create(...)   # step recorded under the trace
    """
    import os
    parent = _trace_var.get()
    trace_id = parent[0] if parent else os.urandom(8).hex()
    token = _trace_var.set((trace_id, name))
    try:
        yield trace_id
    finally:
        _trace_var.reset(token)


def current_trace() -> Optional[tuple]:
    """The active (trace_id, span_name), or None outside any trace()."""
    return _trace_var.get()


# ---- call-site inference ----

# Deny-listed *top-level packages*: promptry itself, provider SDKs, HTTP and
# async plumbing, and common wrapper libs. Matched by package name, never by
# install path — user code legitimately lives in site-packages.
_DENY_PACKAGES = frozenset({
    "promptry", "openai", "anthropic", "litellm", "google", "cohere",
    "mistralai", "boto3", "botocore", "httpx", "httpcore", "aiohttp",
    "requests", "urllib3", "asyncio", "concurrent", "tenacity", "backoff",
    "functools", "contextlib", "threading",
})

# Synthetic code names that don't identify a real call site — keep walking to
# the enclosing named function.
_SKIP_CONAMES = frozenset({
    "<lambda>", "<listcomp>", "<genexpr>", "<dictcomp>", "<setcomp>",
})

_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 1024


def _top_package(module_name: str) -> str:
    return (module_name or "").split(".", 1)[0]


def _qualname(code, frame=None) -> str:
    # co_qualname (3.11+) already encodes class/closure nesting. On 3.10 it does
    # not exist, so reconstruct "Class.method" from the frame when the code is a
    # bound/class method (first arg self/cls) — otherwise the inferred name would
    # silently drop the class on our lowest supported interpreter, forking a
    # prompt's identity between 3.10 and 3.11+. Plain functions keep their bare
    # co_name (co_qualname would too, sans nesting we can't recover here).
    q = getattr(code, "co_qualname", None)
    if q:
        return q
    name = code.co_name
    if frame is not None and code.co_varnames:
        first = code.co_varnames[0]
        if first in ("self", "cls"):
            obj = frame.f_locals.get(first)
            if obj is not None:
                klass = obj if first == "cls" else type(obj)
                klass_name = getattr(klass, "__name__", None)
                if klass_name:
                    return f"{klass_name}.{name}"
    return name


def _is_repl(filename: str) -> bool:
    return (filename in ("<stdin>", "<console>", "<string>")
            or filename.startswith("<ipython-input"))


def _cache_get(code):
    return _cache.get(code)


def _cache_set(code, name: str) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX and code not in _cache:
            # FIFO eviction; key on the code object itself (hashable, and
            # stable — unlike id(), which is reused after GC).
            try:
                _cache.pop(next(iter(_cache)))
            except StopIteration:
                pass
        _cache[code] = name


def _infer_from_stack() -> str:
    frame = sys._getframe(0)
    while frame is not None:
        code = frame.f_code
        module = frame.f_globals.get("__name__", "") or ""
        pkg = _top_package(module)
        # Skip promptry internals + provider/HTTP/async plumbing.
        if pkg in _DENY_PACKAGES:
            frame = frame.f_back
            continue
        # Skip comprehensions/lambdas — walk to the enclosing named function.
        if code.co_name in _SKIP_CONAMES:
            frame = frame.f_back
            continue
        filename = code.co_filename
        if _is_repl(filename):
            return "repl"
        if code.co_name == "<module>":
            return module or "unnamed"
        cached = _cache_get(code)
        if cached is not None:
            return cached
        name = f"{module}:{_qualname(code, frame)}" if module else _qualname(code, frame)
        _cache_set(code, name)
        return name
    return "unnamed"


def infer_task(explicit: Optional[str] = None) -> str:
    """Resolve the prompt name via the precedence ladder. Never raises."""
    if explicit:
        return explicit
    ambient = _task_var.get()
    if ambient:
        return ambient
    try:
        return _infer_from_stack()
    except Exception:
        return "unnamed"
