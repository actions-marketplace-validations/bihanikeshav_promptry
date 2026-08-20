"""LiteLLM capture callback: name inference at pre-call, record + dedup at
success, suppression lifecycle. No network — fake litellm events."""
import types
from datetime import datetime

import pytest

pytest.importorskip("litellm")

from promptry import naming  # noqa: E402
from promptry.integrations import litellm_callback as cb  # noqa: E402
from promptry.registry import PromptRegistry  # noqa: E402
from promptry.storage.sqlite import SQLiteStorage  # noqa: E402


def _resp(model="gpt-4o", pin=100, pout=50, cached=10, content="hi", rid="chatcmpl-x"):
    usage = types.SimpleNamespace(
        prompt_tokens=pin, completion_tokens=pout,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached))
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content=content))
    return types.SimpleNamespace(id=rid, model=model, usage=usage, choices=[choice])


def _kwargs(call_id="c1", task=None, model="gpt-4o", proxy=False):
    md = {}
    if task:
        md["promptry_task"] = task
    params = {"metadata": md}
    if proxy:
        params["proxy_server_request"] = {"x": 1}
    return {
        "litellm_call_id": call_id,
        "model": model,
        "messages": [{"role": "system", "content": "be nice"},
                     {"role": "user", "content": "hello"}],
        "litellm_params": params,
    }


@pytest.fixture
def logger(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    cb._pending.clear()
    lg = cb._build_logger_class()()
    yield lg, st
    st.close()


_T0 = datetime(2020, 1, 1, 0, 0, 0)
_T1 = datetime(2020, 1, 1, 0, 0, 1)  # +1000ms


class TestLifecycle:
    def test_pre_sets_suppression_success_records_and_releases(self, logger):
        lg, st = logger
        kw = _kwargs()
        assert naming.is_suppressed() is False
        lg.log_pre_api_call("gpt-4o", kw["messages"], kw)
        assert naming.is_suppressed() is True          # inner clients stay quiet
        lg.log_success_event(kw, _resp(), _T0, _T1)
        assert naming.is_suppressed() is False          # released after record

        rows = st.list_invocations(days=1)
        assert len(rows) == 1
        inv = st.get_invocation(rows[0]["id"])
        assert inv["metadata"]["model"] == "gpt-4o"
        assert inv["metadata"]["tokens_in"] == 100
        assert inv["metadata"]["cached_tokens"] == 10
        assert inv["metadata"]["latency_ms"] == pytest.approx(1000.0)
        # name inferred from THIS test's call site (no explicit task)
        assert rows[0]["prompt_name"].endswith(
            ":TestLifecycle.test_pre_sets_suppression_success_records_and_releases")
        assert st.get_prompt(rows[0]["prompt_name"]) is not None  # system prompt registered

    def test_metadata_task_overrides_callsite(self, logger):
        lg, st = logger
        kw = _kwargs(task="checkout")
        lg.log_pre_api_call("gpt-4o", kw["messages"], kw)
        lg.log_success_event(kw, _resp(), _T0, _T1)
        assert st.list_invocations(name="checkout", days=1)

    def test_failure_releases_suppression(self, logger):
        lg, st = logger
        kw = _kwargs()
        lg.log_pre_api_call("gpt-4o", kw["messages"], kw)
        assert naming.is_suppressed() is True
        lg.log_failure_event(kw, None, _T0, _T1)
        assert naming.is_suppressed() is False

    def test_proxy_mode_uses_model_name(self, logger):
        lg, st = logger
        kw = _kwargs(proxy=True)
        lg.log_pre_api_call("gpt-4o", kw["messages"], kw)
        lg.log_success_event(kw, _resp(), _T0, _T1)
        assert st.list_invocations(name="litellm:gpt-4o", days=1)


class TestDedup:
    def test_same_response_id_not_double_counted(self, logger):
        lg, st = logger
        r = _resp(rid="chatcmpl-dup")
        for call_id in ("c1", "c2"):
            kw = _kwargs(call_id=call_id)
            lg.log_pre_api_call("gpt-4o", kw["messages"], kw)
            lg.log_success_event(kw, r, _T0, _T1)
        assert st.count_invocations() == 1


class TestEnable:
    def test_enable_is_idempotent(self):
        import litellm
        before = list(litellm.callbacks or [])
        try:
            a = cb.enable_litellm()
            b = cb.enable_litellm()
            assert a is b and a is not None
            assert sum(1 for c in litellm.callbacks if c is a) == 1
        finally:
            litellm.callbacks = before
            cb._enabled_instance = None
