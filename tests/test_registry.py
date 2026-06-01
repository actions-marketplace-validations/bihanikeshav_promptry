import pytest
from promptry.registry import PromptRegistry, track, track_invocation, track_context


class TestPromptRegistry:

    def test_save_and_get(self, registry):
        registry.save("test", "My prompt content")
        fetched = registry.get("test")
        assert fetched.content == "My prompt content"
        assert fetched.version == 1

    def test_version_increments(self, registry):
        r1 = registry.save("test", "Version 1")
        r2 = registry.save("test", "Version 2")
        assert r1.version == 1
        assert r2.version == 2

    def test_dedup(self, registry):
        r1 = registry.save("test", "Same content")
        r2 = registry.save("test", "Same content")
        assert r1.version == r2.version

    def test_save_with_tag(self, registry):
        record = registry.save("test", "Content", tag="prod")
        assert "prod" in record.tags

    def test_tag_existing_version(self, registry):
        registry.save("test", "Content")
        registry.tag("test", 1, "prod")
        assert registry.get_by_tag("test", "prod").version == 1

    def test_tag_nonexistent_raises(self, registry):
        with pytest.raises(ValueError):
            registry.tag("nonexistent", 1, "prod")

    def test_diff(self, registry):
        registry.save("test", "Line one\nLine two\n")
        registry.save("test", "Line one\nLine changed\n")
        diff = registry.diff("test", 1, 2)
        assert "-Line two" in diff
        assert "+Line changed" in diff

    def test_diff_bad_version_raises(self, registry):
        registry.save("test", "Content")
        with pytest.raises(ValueError):
            registry.diff("test", 1, 99)


class TestTrack:

    def _patch_registry(self, monkeypatch, storage):
        monkeypatch.setattr(
            "promptry.registry._default_registry",
            PromptRegistry(storage=storage),
        )

    def test_returns_content_unchanged(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        assert track("Hello world", "test") == "Hello world"

    def test_saves_to_db(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        track("Hello world", "test")
        assert storage.get_prompt("test").content == "Hello world"

    def test_with_tag(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        track("Content", "test", tag="prod")
        record = storage.get_prompt("test")
        assert "prod" in storage.get_tags(record.id)


class TestServedModelCost:
    """A silently rerouted slug (xAI grok-4*-fast -> grok-4.3 on 2026-05-15)
    must be costed at the model the provider actually ran (served_model),
    not the cheap requested slug."""

    def _patch_registry(self, monkeypatch, storage):
        monkeypatch.setattr(
            "promptry.registry._default_registry",
            PromptRegistry(storage=storage),
        )

    def test_prices_off_served_model(self, storage, monkeypatch):
        from promptry.pricing import calculate_cost
        self._patch_registry(monkeypatch, storage)
        track_invocation("rag.answer", metadata={
            "model": "grok-4-fast-non-reasoning",   # requested (retired) slug
            "served_model": "grok-4.3",              # what xAI actually ran
            "tokens_in": 10_000, "tokens_out": 500,
        })
        row = storage.list_invocations(name="rag.answer", days=1)[0]
        assert row["cost"] == pytest.approx(
            calculate_cost("grok-4.3", tokens_in=10_000, tokens_out=500))
        # and decidedly NOT the cheap fast-tier price
        assert row["cost"] != pytest.approx(
            calculate_cost("grok-4-fast-non-reasoning", tokens_in=10_000, tokens_out=500))

    def test_falls_back_to_requested_model_when_no_served(self, storage, monkeypatch):
        from promptry.pricing import calculate_cost
        self._patch_registry(monkeypatch, storage)
        track_invocation("rag.answer", metadata={
            "model": "gpt-4o-mini", "tokens_in": 1000, "tokens_out": 200,
        })
        row = storage.list_invocations(name="rag.answer", days=1)[0]
        assert row["cost"] == pytest.approx(
            calculate_cost("gpt-4o-mini", tokens_in=1000, tokens_out=200))


class TestCaptureTruncation:
    """Captured request/response text is truncated to a configurable cap so the
    trace viewer stays bounded. The cap comes from config by default, an explicit
    arg overrides it, and 0 means store the full text."""

    def _patch_registry(self, monkeypatch, storage):
        monkeypatch.setattr(
            "promptry.registry._default_registry",
            PromptRegistry(storage=storage),
        )

    def _set_capture_limit(self, monkeypatch, n):
        from promptry import config as cfgmod
        cfg = cfgmod.Config()
        cfg.capture.max_chars = n
        monkeypatch.setattr(cfgmod, "get_config", lambda: cfg)

    def _captured(self, storage, name):
        row = storage.list_invocations(name=name, days=1)[0]
        return storage.get_invocation(row["id"])

    def test_explicit_arg_truncates(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        track_invocation("cap.explicit", capture=True,
                         input_text="x" * 100, output_text="y" * 100,
                         max_capture_chars=10)
        rec = self._captured(storage, "cap.explicit")
        assert len(rec["input_text"]) == 10
        assert len(rec["output_text"]) == 10

    def test_default_comes_from_config(self, storage, monkeypatch):
        self._set_capture_limit(monkeypatch, 20)
        self._patch_registry(monkeypatch, storage)
        track_invocation("cap.config", capture=True,
                         input_text="x" * 100)  # no explicit max_capture_chars
        rec = self._captured(storage, "cap.config")
        assert len(rec["input_text"]) == 20

    def test_zero_means_unlimited(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        track_invocation("cap.full", capture=True,
                         input_text="x" * 5000, max_capture_chars=0)
        rec = self._captured(storage, "cap.full")
        assert len(rec["input_text"]) == 5000


class TestTrackContext:

    def _patch_registry(self, monkeypatch, storage):
        monkeypatch.setattr(
            "promptry.registry._default_registry",
            PromptRegistry(storage=storage),
        )

    def test_returns_chunks_unchanged(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        chunks = ["chunk 1", "chunk 2"]
        result = track_context(chunks, "rag-qa")
        assert result == chunks

    def test_saves_joined_content(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        track_context(["chunk 1", "chunk 2"], "rag-qa")
        record = storage.get_prompt("rag-qa:context")
        assert "chunk 1" in record.content
        assert "chunk 2" in record.content

    def test_stores_chunk_count(self, storage, monkeypatch):
        self._patch_registry(monkeypatch, storage)
        track_context(["a", "b", "c"], "rag-qa")
        record = storage.get_prompt("rag-qa:context")
        assert record.metadata["chunk_count"] == 3
