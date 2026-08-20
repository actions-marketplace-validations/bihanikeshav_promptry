"""Drop-in Anthropic client: messages (non-stream, stream, tools, failures)."""
import types

import pytest

from promptry import anthropic as pan
from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage

_MODEL = "claude-3-5-sonnet-20241022"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    yield st
    st.close()


def _resp(text="the answer", rid="msg_1"):
    return types.SimpleNamespace(
        id=rid, model=_MODEL,
        content=[types.SimpleNamespace(type="text", text=text)],
        usage=types.SimpleNamespace(input_tokens=100, output_tokens=20,
                                    cache_read_input_tokens=10,
                                    cache_creation_input_tokens=5))


def _msgs(real):
    return pan._Messages(real, {"task": "claude", "capture": True, "sample_rate": 1.0}, False)


def _inv(storage, name="claude"):
    return storage.get_invocation(storage.list_invocations(name=name, days=1)[0]["id"])


class TestMessages:
    def test_non_streaming(self, storage):
        class _Real:
            def create(self, **kw):
                return _resp()
        _msgs(_Real()).create(model=_MODEL, system="be terse",
                              messages=[{"role": "user", "content": "q"}])
        inv = _inv(storage)
        assert inv["metadata"]["api"] == "messages"
        assert inv["metadata"]["tokens_in"] == 115         # 100 + cache_read 10 + cache_create 5
        assert inv["metadata"]["cached_tokens"] == 10
        assert inv["metadata"]["tokens_out"] == 20
        assert inv["output_text"] == "the answer"
        assert storage.get_prompt("claude") is not None    # system registered

    def test_tool_use_output(self, storage):
        resp = types.SimpleNamespace(
            id="m2", model=_MODEL,
            content=[types.SimpleNamespace(type="tool_use", name="get_weather",
                                           input={"city": "NYC"})],
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=3,
                                        cache_read_input_tokens=0,
                                        cache_creation_input_tokens=0))

        class _Real:
            def create(self, **kw):
                return resp
        _msgs(_Real()).create(model=_MODEL, messages=[{"role": "user", "content": "weather"}])
        assert "get_weather" in _inv(storage)["output_text"]

    def test_streaming(self, storage):
        events = [
            types.SimpleNamespace(type="message_start", message=types.SimpleNamespace(
                id="msg_s", model=_MODEL, usage=types.SimpleNamespace(
                    input_tokens=50, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0))),
            types.SimpleNamespace(type="content_block_delta",
                                  delta=types.SimpleNamespace(text="Hel")),
            types.SimpleNamespace(type="content_block_delta",
                                  delta=types.SimpleNamespace(text="lo")),
            types.SimpleNamespace(type="message_delta",
                                  usage=types.SimpleNamespace(output_tokens=2)),
            types.SimpleNamespace(type="message_stop"),
        ]

        class _Real:
            def create(self, **kw):
                return iter(events)
        stream = _msgs(_Real()).create(model=_MODEL, system="s",
                                       messages=[{"role": "user", "content": "hi"}],
                                       stream=True)
        list(stream)
        inv = _inv(storage)
        assert inv["output_text"] == "Hello"
        assert inv["metadata"]["tokens_in"] == 50
        assert inv["metadata"]["tokens_out"] == 2

    def test_failure_recorded_and_reraised(self, storage):
        class _Boom:
            def create(self, **kw):
                raise RuntimeError("api down")
        with pytest.raises(RuntimeError):
            _msgs(_Boom()).create(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
        inv = _inv(storage)
        assert inv["metadata"]["status"] == "error"
        assert "api down" in inv["metadata"]["error"]


class TestSystemBlocks:
    def test_system_as_list_of_blocks(self):
        txt = pan._system_text({"system": [{"type": "text", "text": "you are terse"},
                                           {"type": "text", "text": "and kind"}]})
        assert txt == "you are terse\nand kind"

    def test_system_as_string(self):
        assert pan._system_text({"system": "plain"}) == "plain"

    def test_no_system(self):
        assert pan._system_text({}) is None
