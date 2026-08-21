"""Capture every LiteLLM call — 100+ providers — through one callback.

LiteLLM is a universal gateway, so a single ``CustomLogger`` captures whatever
providers a user routes through it (OpenAI, Anthropic, Gemini, Bedrock, Mistral,
Cohere, …) with cost, tokens, cached-token signal, and the prompt.

    import promptry
    promptry.enable_litellm()          # once, at startup
    import litellm
    litellm.completion(model="claude-3-5-sonnet-20241022", messages=[...])

Design (per the Fable spec):
* Name is inferred at ``log_pre_api_call`` — the user's frame that called
  ``litellm.completion`` is still on the stack there (success fires later, after
  streaming, when the stack is gone). It's stashed by ``litellm_call_id``.
* A suppression ContextVar is set at pre-call so an inner instrumented client
  (promptry.openai) stays quiet and lets this outer, provider-normalizing layer
  own the call. The provider ``response.id`` is the belt-and-suspenders dedup.
* Everything is best-effort: the callback never raises into the user's call.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from promptry import naming

logger = logging.getLogger("promptry.integrations.litellm")

# name + suppression token, stashed at pre-call, consumed at success. Keyed by
# litellm_call_id and bounded so a lost success event can't leak memory.
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()
_PENDING_MAX = 4096


def _stash(call_id: str, data: dict) -> None:
    with _pending_lock:
        if len(_pending) >= _PENDING_MAX and call_id not in _pending:
            try:
                _pending.pop(next(iter(_pending)))
            except StopIteration:
                pass
        _pending[call_id] = data


def _pop(call_id: str | None) -> dict | None:
    if not call_id:
        return None
    with _pending_lock:
        return _pending.pop(call_id, None)


def _messages_text(messages) -> str | None:
    parts = []
    for m in messages or []:
        if isinstance(m, dict):
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            parts.append(f"{m.get('role', '')}: {c}")
    return "\n".join(parts) if parts else None


def _system_prompt(messages) -> str | None:
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            c = m.get("content", "")
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return c if isinstance(c, str) else None
    return None


def _response_text(response_obj) -> str | None:
    try:
        return response_obj.choices[0].message.content or None
    except Exception:
        return None


def _usage_meta(response_obj, model) -> dict:
    meta: dict[str, Any] = {"provider": "litellm"}
    if model:
        meta["model"] = model
    usage = getattr(response_obj, "usage", None)
    if usage is not None:
        pin = getattr(usage, "prompt_tokens", None)
        pout = getattr(usage, "completion_tokens", None)
        if pin is not None:
            meta["tokens_in"] = pin
        if pout is not None:
            meta["tokens_out"] = pout
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details is not None else 0
        cached = int(cached or 0)
        meta["cached_tokens"] = cached
        # Cache-*write* (Anthropic prompt-cache creation) is priced higher than
        # base input; litellm exposes it on the details or the usage object.
        # Without it, those tokens fold into uncached input and get under-billed.
        cw = getattr(details, "cache_creation_tokens", None) if details is not None else None
        if cw is None:
            cw = getattr(usage, "_cache_creation_input_tokens", None)
        cw = int(cw or 0)
        # Only record when prompt_tokens genuinely includes it, so pricing's
        # `uncached = tokens_in - cached - cache_write` can't go negative if a
        # given litellm mapping reports input tokens exclusive of cache writes.
        if cw and pin is not None and pin >= cached + cw:
            meta["cache_write_tokens"] = cw
    return meta


def _build_logger_class():
    """Build the CustomLogger subclass lazily so importing this module never
    hard-requires litellm."""
    from litellm.integrations.custom_logger import CustomLogger

    class PromptryLogger(CustomLogger):
        def log_pre_api_call(self, model, messages, kwargs):
            try:
                params = kwargs.get("litellm_params") or {}
                md = params.get("metadata") or {}
                explicit = md.get("promptry_task")
                if params.get("proxy_server_request") and not explicit:
                    # In proxy-server mode the call site is the proxy handler,
                    # not user code — inference would be meaningless.
                    name = f"litellm:{model}"
                else:
                    name = naming.infer_task(explicit)
                token = naming.suppress_capture()
                call_id = kwargs.get("litellm_call_id")
                if call_id:
                    _stash(call_id, {"name": name, "token": token})
                else:
                    # No id to correlate on — release immediately, rely on the
                    # response-id dedup instead of suppression.
                    naming.resume_capture(token)
            except Exception:
                logger.debug("promptry pre-call hook failed", exc_info=True)

        def _name_for(self, kwargs, stashed):
            params = kwargs.get("litellm_params") or {}
            return stashed.get("name") or naming.infer_task(
                (params.get("metadata") or {}).get("promptry_task"))

        def _record(self, kwargs, response_obj, start_time, end_time):
            stashed = _pop(kwargs.get("litellm_call_id")) or {}
            try:
                name = self._name_for(kwargs, stashed)
                # Streaming: litellm hands the assembled response via
                # complete_streaming_response when the chunk object has no usage.
                resp = response_obj
                if getattr(resp, "usage", None) is None:
                    csr = kwargs.get("complete_streaming_response")
                    if csr is not None:
                        resp = csr
                response_obj = resp
                model = getattr(response_obj, "model", None) or kwargs.get("model")
                meta = _usage_meta(response_obj, model)
                try:
                    meta["latency_ms"] = (end_time - start_time).total_seconds() * 1000
                except Exception:
                    pass
                cost = kwargs.get("response_cost")
                if cost is not None:
                    meta["cost"] = cost
                messages = kwargs.get("messages") or []
                from promptry.registry import track, track_invocation
                track_invocation(
                    name, metadata=meta,
                    input_text=_messages_text(messages),
                    output_text=_response_text(response_obj),
                    response_id=getattr(response_obj, "id", None),
                )
                system = _system_prompt(messages)
                if system:
                    track(system, name, metadata={"model": model})
            except Exception:
                logger.debug("promptry success hook failed", exc_info=True)
            finally:
                if stashed.get("token") is not None:
                    naming.resume_capture(stashed["token"])

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._record(kwargs, response_obj, start_time, end_time)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._record(kwargs, response_obj, start_time, end_time)

        def _record_failure(self, kwargs, response_obj):
            """Record a failed call so error rate / failed-call volume is visible."""
            stashed = _pop(kwargs.get("litellm_call_id")) or {}
            try:
                name = self._name_for(kwargs, stashed)
                err = kwargs.get("exception") or response_obj
                meta = {"provider": "litellm", "model": kwargs.get("model"),
                        "status": "error",
                        "error": (f"{type(err).__name__}: {str(err)[:300]}"
                                  if err is not None else "error")}
                from promptry.registry import track_invocation
                track_invocation(name, metadata=meta,
                                 input_text=_messages_text(kwargs.get("messages") or []))
            except Exception:
                logger.debug("promptry failure hook failed", exc_info=True)
            finally:
                if stashed.get("token") is not None:
                    naming.resume_capture(stashed["token"])

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._record_failure(kwargs, response_obj)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._record_failure(kwargs, response_obj)

    return PromptryLogger


_enabled_instance = None
_enable_lock = threading.Lock()


def enable_litellm():
    """Register promptry as a LiteLLM callback (idempotent). Returns the logger
    instance, or None if litellm isn't installed."""
    global _enabled_instance
    with _enable_lock:
        if _enabled_instance is not None:
            return _enabled_instance
        try:
            import litellm
        except ImportError:
            logger.warning("enable_litellm(): litellm is not installed")
            return None
        instance = _build_logger_class()()
        if instance not in (litellm.callbacks or []):
            litellm.callbacks = list(litellm.callbacks or []) + [instance]
        _enabled_instance = instance
        return instance
