"""The legacy patch_* integrations are now thin, deprecated shims over the
shared capture core: they emit a DeprecationWarning and record the full cost
ledger (not just the system prompt, as the old track()-only version did)."""
from __future__ import annotations

import asyncio
import types

import pytest

from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    yield st
    st.close()


def _openai_response():
    return types.SimpleNamespace(
        id="chatcmpl-1", model="gpt-4o",
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=20,
                                    prompt_tokens_details=types.SimpleNamespace(cached_tokens=0)),
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi"))])


class _FakeChat:
    def __init__(self, create):
        self.completions = types.SimpleNamespace(create=create)


class _FakeOpenAI:
    def __init__(self, create):
        self.chat = _FakeChat(create)


class TestPatchOpenAI:
    def test_deprecated_and_writes_ledger(self, storage):
        from promptry.integrations.openai import patch_openai
        client = _FakeOpenAI(lambda **kw: _openai_response())
        with pytest.warns(DeprecationWarning):
            patch_openai(client, prompt_name="svc")
        resp = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "q"}])
        assert resp.choices[0].message.content == "hi"      # unchanged
        rows = storage.list_invocations(name="svc", days=1)
        assert len(rows) == 1
        assert storage.get_invocation(rows[0]["id"])["metadata"]["tokens_in"] == 10

    def test_async_client(self, storage):
        from promptry.integrations.openai import patch_openai

        async def _create(**kw):
            return _openai_response()
        client = _FakeOpenAI(_create)
        with pytest.warns(DeprecationWarning):
            patch_openai(client, prompt_name="asvc")

        async def _run():
            return await client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "q"}])
        asyncio.run(_run())
        assert storage.list_invocations(name="asvc", days=1)


def _anthropic_response():
    return types.SimpleNamespace(
        id="msg_1", model="claude-3-5-sonnet-20241022",
        content=[types.SimpleNamespace(type="text", text="hi")],
        usage=types.SimpleNamespace(input_tokens=100, output_tokens=20,
                                    cache_read_input_tokens=0, cache_creation_input_tokens=0))


class TestPatchAnthropic:
    def test_deprecated_and_writes_ledger(self, storage):
        from promptry.integrations.anthropic import patch_anthropic
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **kw: _anthropic_response()))
        with pytest.warns(DeprecationWarning):
            patch_anthropic(client, prompt_name="claude-svc")
        client.messages.create(model="claude-3-5-sonnet-20241022", system="s",
                               messages=[{"role": "user", "content": "q"}])
        rows = storage.list_invocations(name="claude-svc", days=1)
        assert len(rows) == 1
        assert storage.get_invocation(rows[0]["id"])["metadata"]["tokens_in"] == 100


class TestPatchLiteLLM:
    def test_deprecated_and_forwards_to_enable_litellm(self, monkeypatch):
        pytest.importorskip("litellm")
        import litellm
        from promptry.integrations import litellm_callback as cb
        monkeypatch.setattr(litellm, "callbacks", [], raising=False)
        monkeypatch.setattr(cb, "_enabled_instance", None, raising=False)
        from promptry.integrations.litellm import patch_litellm
        with pytest.warns(DeprecationWarning):
            patch_litellm()
        assert cb._enabled_instance is not None
        assert cb._enabled_instance in litellm.callbacks
