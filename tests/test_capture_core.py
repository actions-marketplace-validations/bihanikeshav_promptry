"""Shared capture core: record_call + StreamRecorder, provider-agnostic."""
import pytest

from promptry import _capture as core
from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    yield st
    st.close()


def _row(storage, name):
    rows = storage.list_invocations(name=name, days=1)
    return storage.get_invocation(rows[0]["id"]) if rows else None


class TestRecordCall:
    def test_basic_with_computed_cost(self, storage):
        core.record_call(core.CallRecord(
            name="bot", provider="openai", api="chat", model="gpt-4o",
            input_tokens=100, output_tokens=50, cached_tokens=10,
            system_prompt="be nice", input_text="hi", output_text="yo",
            response_id="r1"), capture=True)
        inv = _row(storage, "bot")
        assert inv["metadata"]["model"] == "gpt-4o"
        assert inv["metadata"]["tokens_in"] == 100
        assert inv["metadata"]["cost"] is not None      # computed from price DB
        assert inv["input_text"] == "hi" and inv["output_text"] == "yo"
        assert storage.get_prompt("bot") is not None    # system prompt registered

    def test_provider_cost_wins(self, storage):
        core.record_call(core.CallRecord(
            name="bot", provider="litellm", api="chat", model="gpt-4o",
            input_tokens=1, output_tokens=1, provider_cost=0.4242))
        assert _row(storage, "bot")["metadata"]["cost"] == 0.4242

    def test_embedding_row_input_only(self, storage):
        # no output text, no completion tokens — still a cost row
        core.record_call(core.CallRecord(
            name="emb", provider="openai", api="embeddings",
            model="text-embedding-3-small", input_tokens=1000,
            output_tokens=None, metadata={"batch": 3}))
        inv = _row(storage, "emb")
        assert inv["metadata"]["api"] == "embeddings"
        assert inv["metadata"]["tokens_in"] == 1000
        assert "tokens_out" not in inv["metadata"]
        assert inv["metadata"]["batch"] == 3
        assert inv["output_text"] is None

    def test_error_status_recorded(self, storage):
        core.record_call(core.CallRecord(
            name="bot", provider="openai", api="chat", model="gpt-4o",
            status="error", error="RuntimeError: boom"))
        inv = _row(storage, "bot")
        assert inv["metadata"]["status"] == "error"
        assert "boom" in inv["metadata"]["error"]

    def test_never_raises(self):
        # a bad record (no registry storage side effects needed) must not raise
        core.record_call(core.CallRecord(name="x", provider="p", api="a"))


class TestStreamRecorder:
    def _adapter(self, event):
        # event is a (text, usage_only, tokens, rid) tuple in this fake
        text, usage_only, itok, otok, rid = event
        return core.StreamDelta(text=text, input_tokens=itok, output_tokens=otok,
                                response_id=rid, model="gpt-4o", is_usage_only=usage_only)

    def test_accumulates_and_records(self, storage):
        base = core.CallRecord(name="s", provider="openai", api="chat",
                               input_text="hi", system_prompt="sys")
        rec = core.StreamRecorder(base, self._adapter, capture=True, sample_rate=1.0,
                                  swallow_usage_only=True)
        events = [("Hel", False, None, None, "rid"),
                  ("lo", False, None, None, None),
                  (None, True, 10, 2, None)]        # injected usage-only -> swallowed
        yielded = [e for e in events if rec.absorb(e)]
        rec.finish()
        assert len(yielded) == 2                     # usage-only swallowed
        inv = _row(storage, "s")
        assert inv["output_text"] == "Hello"
        assert inv["metadata"]["tokens_in"] == 10

    def test_abandoned_stream_no_garbage_row(self, storage):
        base = core.CallRecord(name="s2", provider="openai", api="chat")
        rec = core.StreamRecorder(base, self._adapter, capture=False, sample_rate=1.0)
        rec.finish()                                  # nothing absorbed
        assert _row(storage, "s2") is None
