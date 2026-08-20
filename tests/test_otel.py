"""OpenTelemetry export: captured calls become gen_ai spans in the customer's
own collector. Tested with an in-memory exporter (no network)."""
import pytest

from promptry import otel
from promptry import _capture as core
from promptry.registry import PromptRegistry
from promptry.storage.sqlite import SQLiteStorage

pytest.importorskip("opentelemetry.sdk")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    st = SQLiteStorage(tmp_path / "t.db")
    monkeypatch.setattr("promptry.registry._default_registry", PromptRegistry(storage=st))
    yield st
    st.close()


@pytest.fixture
def exporter():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    otel._reset_for_tests()
    assert otel.enable_otel(exporter=exp, simple=True) is True
    yield exp
    otel._reset_for_tests()


class TestSpanAttributes:
    def test_maps_gen_ai_conventions(self):
        rec = core.CallRecord(name="bot", provider="openai", api="chat",
                              model="gpt-4o", input_tokens=100, output_tokens=50,
                              cached_tokens=10, provider_cost=0.01,
                              trace_id="t1", span_name="agent")
        a = otel._span_attributes(rec)
        assert a["gen_ai.system"] == "openai"
        assert a["gen_ai.request.model"] == "gpt-4o"
        assert a["gen_ai.usage.input_tokens"] == 100
        assert a["promptry.cost_usd"] == 0.01
        assert a["promptry.trace_id"] == "t1"

    def test_omits_none(self):
        rec = core.CallRecord(name="e", provider="openai", api="embeddings",
                              model="text-embedding-3-small", input_tokens=1000)
        a = otel._span_attributes(rec)
        assert "gen_ai.usage.output_tokens" not in a   # embeddings: no output


class TestExport:
    def test_record_call_emits_span(self, storage, exporter):
        core.record_call(core.CallRecord(
            name="bot", provider="openai", api="chat", model="gpt-4o",
            input_tokens=100, output_tokens=50, latency_ms=1234))
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "chat gpt-4o"
        assert span.attributes["gen_ai.usage.input_tokens"] == 100

    def test_error_span_status(self, storage, exporter):
        from opentelemetry.trace import StatusCode
        core.record_call(core.CallRecord(
            name="bot", provider="openai", api="chat", model="gpt-4o",
            status="error", error="RuntimeError: boom"))
        span = exporter.get_finished_spans()[0]
        assert span.status.status_code == StatusCode.ERROR

    def test_disabled_emits_nothing(self, storage):
        otel._reset_for_tests()
        assert otel.otel_enabled() is False
        # not enabled -> record_call must not emit or raise
        core.record_call(core.CallRecord(name="b", provider="openai", api="chat",
                                         model="gpt-4o", input_tokens=1))
        assert otel.otel_enabled() is False
