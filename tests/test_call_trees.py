"""Cost-attributed call trees: promptry.trace groups captured calls; storage
returns the per-step waterfall. SQLite-only, zero infra."""
import types

import pytest

from promptry import naming, trace
from promptry import openai as pio
from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    yield st
    st.close()


def _resp(rid, pin=100, pout=50):
    return types.SimpleNamespace(
        id=rid, model="gpt-4o",
        usage=types.SimpleNamespace(prompt_tokens=pin, completion_tokens=pout,
                                    prompt_tokens_details=types.SimpleNamespace(cached_tokens=0)),
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))])


class _Fake:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kw):
        return self._responses.pop(0)


class TestTraceContext:
    def test_nesting_shares_trace_id(self):
        assert naming.current_trace() is None
        with trace("outer") as outer:
            assert naming.current_trace() == (outer, "outer")
            with trace("inner") as inner:
                assert inner == outer            # nested shares the outer id
                assert naming.current_trace() == (outer, "inner")
            assert naming.current_trace() == (outer, "outer")
        assert naming.current_trace() is None


class TestCallTree:
    def test_calls_grouped_and_waterfall(self, storage):
        tc = pio._TrackedCompletions(_Fake([_resp("r1"), _resp("r2")]),
                                     {"task": "agent", "capture": False, "sample_rate": 1.0})
        with trace("checkout_agent") as tid:
            tc.create(model="gpt-4o", messages=[{"role": "user", "content": "step 1"}])
            tc.create(model="gpt-4o", messages=[{"role": "user", "content": "step 2"}])

        steps = storage.get_trace(tid)
        assert len(steps) == 2                    # both calls attributed to the trace
        assert all(s["span_name"] == "checkout_agent" for s in steps)
        assert all(s["cost"] is not None for s in steps)

        traces = storage.list_traces(days=1)
        assert len(traces) == 1
        assert traces[0]["trace_id"] == tid
        assert traces[0]["steps"] == 2
        assert traces[0]["tokens_in"] == 200      # 100 + 100
        assert traces[0]["cost"] > 0

    def test_calls_outside_a_trace_are_untraced(self, storage):
        tc = pio._TrackedCompletions(_Fake([_resp("r3")]),
                                     {"task": "solo", "capture": False, "sample_rate": 1.0})
        tc.create(model="gpt-4o", messages=[{"role": "user", "content": "x"}])
        assert storage.list_traces(days=1) == []   # no trace_id -> not grouped

    def test_streaming_calls_are_traced(self, storage):
        def _chunks():
            yield types.SimpleNamespace(model="gpt-4o", id="s1",
                choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content="hi"))],
                usage=None)
            yield types.SimpleNamespace(model="gpt-4o", id="s1", choices=[],
                usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=1,
                    prompt_tokens_details=types.SimpleNamespace(cached_tokens=0)))

        class _StreamFake:
            def create(self, **kw):
                return _chunks()
        tc = pio._TrackedCompletions(_StreamFake(),
                                     {"task": "s", "capture": False, "sample_rate": 1.0})
        with trace("streamed_agent") as tid:
            stream = tc.create(model="gpt-4o", messages=[{"role": "user", "content": "x"}],
                               stream=True)
        list(stream)  # consumed after the trace block exits — trace captured at start
        steps = storage.get_trace(tid)
        assert len(steps) == 1 and steps[0]["span_name"] == "streamed_agent"
