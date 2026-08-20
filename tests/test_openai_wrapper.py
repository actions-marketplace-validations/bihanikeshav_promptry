"""Drop-in OpenAI wrapper: records cost/tokens/prompt without touching network."""
import types

import pytest

from promptry import openai as pio
from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage


def _fake_response(model="gpt-4o", pin=100, pout=50, cached=20, content="hi there",
                   rid="chatcmpl-abc"):
    usage = types.SimpleNamespace(
        prompt_tokens=pin, completion_tokens=pout, total_tokens=pin + pout,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached))
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content=content))
    return types.SimpleNamespace(id=rid, model=model, usage=usage, choices=[choice])


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls = []
        self.some_real_attr = "proxied"

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _stream_chunks(pieces=("Hel", "lo"), rid="chatcmpl-s", model="gpt-4o",
                   with_usage=True):
    for p in pieces:
        yield types.SimpleNamespace(
            model=model, id=rid,
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=p))],
            usage=None)
    if with_usage:
        yield types.SimpleNamespace(
            model=model, id=rid, choices=[],
            usage=types.SimpleNamespace(
                prompt_tokens=10, completion_tokens=2, total_tokens=12,
                prompt_tokens_details=types.SimpleNamespace(cached_tokens=0)))


class _FakeStreamCompletions:
    def __init__(self, gen_factory):
        self._gen_factory = gen_factory
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._gen_factory()


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

    def test_streaming_captures_on_completion(self, storage):
        fc = _FakeStreamCompletions(lambda: _stream_chunks())
        tc = pio._TrackedCompletions(fc, {"task": "bot", "capture": True, "sample_rate": 1.0})
        stream = tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
                           stream=True)
        assert storage.count_invocations() == 0  # nothing until consumed
        chunks = list(stream)
        # include_usage was injected, and the usage-only chunk was swallowed
        assert fc.last_kwargs["stream_options"] == {"include_usage": True}
        assert all(c.choices for c in chunks)
        rows = storage.list_invocations(name="bot", days=1)
        assert len(rows) == 1
        inv = storage.get_invocation(rows[0]["id"])
        assert inv["metadata"]["tokens_in"] == 10
        assert inv["output_text"] == "Hello"

    def test_streaming_respects_user_stream_options(self, storage):
        fc = _FakeStreamCompletions(lambda: _stream_chunks())
        tc = pio._TrackedCompletions(fc, {"task": "bot", "capture": False, "sample_rate": 1.0})
        stream = tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
                           stream=True, stream_options={"include_usage": True})
        chunks = list(stream)
        # user asked for usage -> the usage-only chunk is NOT swallowed
        assert any(not c.choices for c in chunks)
        assert storage.count_invocations() == 1

    def test_tool_call_output_is_captured(self, storage):
        msg = types.SimpleNamespace(content=None, tool_calls=[
            types.SimpleNamespace(function=types.SimpleNamespace(
                name="get_weather", arguments='{"city":"NYC"}'))])
        resp = types.SimpleNamespace(
            id="r1", model="gpt-4o",
            usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=3,
                prompt_tokens_details=types.SimpleNamespace(cached_tokens=0)),
            choices=[types.SimpleNamespace(message=msg)])
        tc = pio._TrackedCompletions(_FakeCompletions(resp),
                                     {"task": "bot", "capture": True, "sample_rate": 1.0})
        tc.create(model="gpt-4o", messages=[{"role": "user", "content": "weather?"}])
        inv = storage.get_invocation(storage.list_invocations(name="bot", days=1)[0]["id"])
        assert "get_weather" in inv["output_text"]

    def test_failed_call_is_recorded_and_reraised(self, storage):
        class _Boom:
            def create(self, **kw):
                raise RuntimeError("api down")
        tc = pio._TrackedCompletions(_Boom(),
                                     {"task": "bot", "capture": False, "sample_rate": 1.0})
        with pytest.raises(RuntimeError):
            tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        rows = storage.list_invocations(name="bot", days=1)
        assert len(rows) == 1
        inv = storage.get_invocation(rows[0]["id"])
        assert inv["metadata"]["status"] == "error"
        assert "api down" in inv["metadata"]["error"]

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

    def test_name_is_inferred_from_callsite_when_unset(self, storage):
        # task=None -> infer from the call site (this test method)
        tc = pio._TrackedCompletions(_FakeCompletions(_fake_response()),
                                     {"task": None, "capture": False, "sample_rate": 1.0})
        tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        rows = storage.list_invocations(days=1)
        assert len(rows) == 1
        assert rows[0]["prompt_name"].endswith(
            ":TestRecording.test_name_is_inferred_from_callsite_when_unset")

    def test_response_id_dedups_across_two_records(self, storage):
        resp = _fake_response(rid="chatcmpl-dup")
        tc = pio._TrackedCompletions(_FakeCompletions(resp),
                                     {"task": "bot", "capture": False, "sample_rate": 1.0})
        tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])  # same id
        assert storage.count_invocations() == 1

    def test_suppressed_context_skips_recording(self, storage):
        tc = pio._TrackedCompletions(_FakeCompletions(_fake_response()),
                                     {"task": "bot", "capture": False, "sample_rate": 1.0})
        from promptry import naming
        token = naming.suppress_capture()
        try:
            tc.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        finally:
            naming.resume_capture(token)
        assert storage.count_invocations() == 0


class TestEmbeddings:
    def test_embeddings_recorded_as_cost_row(self, storage):
        resp = types.SimpleNamespace(model="text-embedding-3-small",
                                     usage=types.SimpleNamespace(prompt_tokens=1000))

        class _RealEmb:
            def create(self, **kw):
                return resp
        emb = pio._Embeddings(_RealEmb(), {"task": "emb", "capture": False,
                                           "sample_rate": 1.0}, False)
        emb.create(model="text-embedding-3-small", input=["a", "b", "c"])
        inv = storage.get_invocation(storage.list_invocations(name="emb", days=1)[0]["id"])
        assert inv["metadata"]["api"] == "embeddings"
        assert inv["metadata"]["tokens_in"] == 1000
        assert inv["metadata"]["batch"] == 3
        assert "tokens_out" not in inv["metadata"]
        assert inv["output_text"] is None


class TestResponsesApi:
    def test_non_streaming_recorded(self, storage):
        resp = types.SimpleNamespace(
            id="resp_1", model="gpt-4o", output_text="the answer",
            usage=types.SimpleNamespace(input_tokens=20, output_tokens=5,
                input_tokens_details=types.SimpleNamespace(cached_tokens=2)))

        class _RealResp:
            def create(self, **kw):
                return resp
        r = pio._Responses(_RealResp(), {"task": "resp", "capture": True,
                                         "sample_rate": 1.0}, False)
        r.create(model="gpt-4o", input="hello", instructions="be terse")
        inv = storage.get_invocation(storage.list_invocations(name="resp", days=1)[0]["id"])
        assert inv["metadata"]["api"] == "responses"
        assert inv["metadata"]["tokens_in"] == 20 and inv["metadata"]["tokens_out"] == 5
        assert inv["output_text"] == "the answer"
        assert storage.get_prompt("resp") is not None  # instructions registered as system

    def test_streaming_recorded(self, storage):
        events = [
            types.SimpleNamespace(type="response.created",
                response=types.SimpleNamespace(id="resp_s", model="gpt-4o", usage=None)),
            types.SimpleNamespace(type="response.output_text.delta", delta="Hel"),
            types.SimpleNamespace(type="response.output_text.delta", delta="lo"),
            types.SimpleNamespace(type="response.completed",
                response=types.SimpleNamespace(id="resp_s", model="gpt-4o",
                    usage=types.SimpleNamespace(input_tokens=10, output_tokens=2,
                        input_tokens_details=None))),
        ]

        class _RealResp:
            def create(self, **kw):
                return iter(events)
        r = pio._Responses(_RealResp(), {"task": "rs", "capture": True,
                                         "sample_rate": 1.0}, False)
        stream = r.create(model="gpt-4o", input="hi", stream=True)
        consumed = list(stream)
        assert len(consumed) == 4                       # no events swallowed
        inv = storage.get_invocation(storage.list_invocations(name="rs", days=1)[0]["id"])
        assert inv["output_text"] == "Hello"
        assert inv["metadata"]["tokens_in"] == 10


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
