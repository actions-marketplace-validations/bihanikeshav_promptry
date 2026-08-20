"""Drop-in OpenAI wrapper: records cost/tokens/prompt without touching network."""
import types

import pytest

from promptry import openai as pio
from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage


def _fake_response(model="gpt-4o", pin=100, pout=50, cached=20, content="hi there"):
    usage = types.SimpleNamespace(
        prompt_tokens=pin, completion_tokens=pout, total_tokens=pin + pout,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached))
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content=content))
    return types.SimpleNamespace(model=model, usage=usage, choices=[choice])


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls = []
        self.some_real_attr = "proxied"

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


@pytest.fixture
def storage(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    yield st
    st.close()


class TestExtraction:
    def test_messages_text(self):
        txt = pio._messages_text({"messages": [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hello"},
        ]})
        assert "system: be nice" in txt and "user: hello" in txt

    def test_output_text(self):
        assert pio._output_text(_fake_response(content="yo")) == "yo"
        assert pio._output_text(types.SimpleNamespace()) is None


class TestRecording:
    def test_records_invocation_with_cost_and_capture(self, storage):
        tc = pio._TrackedCompletions(_FakeCompletions(_fake_response()),
                                     {"task": "bot", "capture": True, "sample_rate": 1.0})
        tc.create(model="gpt-4o", messages=[
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hello"},
        ])
        rows = storage.list_invocations(name="bot", days=1)
        assert len(rows) == 1
        inv = storage.get_invocation(rows[0]["id"])
        assert inv["metadata"]["model"] == "gpt-4o"
        assert inv["metadata"]["tokens_in"] == 100
        assert inv["metadata"].get("cost") is not None  # auto-computed from pricing
        assert inv["metadata"]["cached_tokens"] == 20    # prefix-cache signal preserved
        assert "user: hello" in inv["input_text"]
        # the system prompt is registered for the registry + cache optimizer
        assert storage.get_prompt("bot") is not None

    def test_streaming_is_not_recorded(self, storage):
        tc = pio._TrackedCompletions(_FakeCompletions(_fake_response()),
                                     {"task": "bot", "capture": False, "sample_rate": 1.0})
        tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True)
        assert storage.list_invocations(name="bot", days=1) == []

    def test_tracking_failure_never_breaks_the_call(self, storage, monkeypatch):
        monkeypatch.setattr(pio, "_extract_usage_metadata",
                            lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
        fake = _FakeCompletions(_fake_response())
        tc = pio._TrackedCompletions(fake, {"task": "bot", "capture": False, "sample_rate": 1.0})
        resp = tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        assert resp is fake._response  # response returned despite tracking error

    def test_completions_proxies_unknown_attrs(self, storage):
        tc = pio._TrackedCompletions(_FakeCompletions(_fake_response()),
                                     {"task": "bot", "capture": False, "sample_rate": 1.0})
        assert tc.some_real_attr == "proxied"


class TestResponseIdDedup:
    """A provider response id dedups the same call seen by two capture layers."""

    def test_duplicate_response_id_is_ignored(self, storage):
        a = storage.record_invocation("p", metadata={"cost": 0.01, "model": "gpt-4o"},
                                      response_id="chatcmpl-1")
        b = storage.record_invocation("p", metadata={"cost": 0.01, "model": "gpt-4o"},
                                      response_id="chatcmpl-1")  # same call, other layer
        assert a > 0 and b == 0
        assert storage.count_invocations() == 1

    def test_distinct_ids_and_null_ids_both_land(self, storage):
        storage.record_invocation("p", metadata={"model": "gpt-4o"}, response_id="a")
        storage.record_invocation("p", metadata={"model": "gpt-4o"}, response_id="b")
        # null response_id is never deduped (most calls have no id)
        storage.record_invocation("p", metadata={"model": "gpt-4o"})
        storage.record_invocation("p", metadata={"model": "gpt-4o"})
        assert storage.count_invocations() == 4


class TestDropIn:
    def test_constructs_and_wraps_and_proxies(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-xxx")
        client = pio.OpenAI(promptry_task="x")
        assert isinstance(client.chat.completions, pio._TrackedCompletions)
        # unknown attributes fall through to the real client
        assert client.api_key == "sk-test-xxx"

    def test_async_dropin_wraps_async_completions(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-xxx")
        client = pio.AsyncOpenAI(promptry_task="x")
        assert isinstance(client.chat.completions, pio._AsyncTrackedCompletions)
