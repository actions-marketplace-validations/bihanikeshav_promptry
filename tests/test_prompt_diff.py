"""Tests for promptry.prompt_diff (cross-prompt diff + cache analysis)."""
from __future__ import annotations

from promptry.prompt_diff import (
    cache_analysis,
    diff_prompts,
    prefix_cache_analysis,
    shorten_analysis,
)


def test_diff_prompts_identical():
    text = "You are a helpful assistant."
    segs = diff_prompts(text, text)
    assert segs == [{"type": "equal", "text": text}]


def test_diff_prompts_insert_and_delete():
    a = "You are a helpful assistant. Answer briefly."
    b = "You are a helpful assistant. Answer in detail."
    segs = diff_prompts(a, b)

    # Reconstruct each side from the opcodes and confirm they round-trip.
    reconstructed_a = "".join(s["text"] for s in segs if s["type"] in ("equal", "delete"))
    reconstructed_b = "".join(s["text"] for s in segs if s["type"] in ("equal", "insert"))
    assert reconstructed_a == a
    assert reconstructed_b == b

    types = {s["type"] for s in segs}
    assert "delete" in types
    assert "insert" in types
    # The long shared preamble should show up as an equal segment.
    assert any(s["type"] == "equal" and "You are a helpful assistant." in s["text"] for s in segs)


def test_diff_prompts_empty_inputs():
    assert diff_prompts("", "") == []
    segs = diff_prompts("", "hello")
    assert segs == [{"type": "insert", "text": "hello"}]
    segs = diff_prompts("hello", "")
    assert segs == [{"type": "delete", "text": "hello"}]


def test_cache_analysis_shared_prefix_math():
    a = "SYSTEM: You are a support bot.\nUSER: my name is Alice, help me."
    b = "SYSTEM: You are a support bot.\nUSER: my name is Bob, help me."
    result = cache_analysis(a, b)

    expected_prefix = len("SYSTEM: You are a support bot.\nUSER: my name is ")
    assert result["shared_prefix_chars"] == expected_prefix
    shorter_len = min(len(a), len(b))
    assert result["shared_prefix_ratio"] == round(expected_prefix / shorter_len, 4)
    assert 0.0 <= result["shared_prefix_ratio"] <= 1.0


def test_cache_analysis_suggested_true_for_large_shared_prefix():
    shared = "You are a helpful, careful, detail-oriented assistant who always double checks facts. "
    a = shared + "Format: bullet points."
    b = shared + "Format: numbered list."
    result = cache_analysis(a, b)

    assert result["suggested"] is True
    # Coincidental extra overlap ("Format: ") beyond `shared` is fine; the
    # prefix must be at least as long as the deliberately shared preamble.
    assert result["shared_prefix_chars"] >= len(shared)
    assert "rationale" in result and isinstance(result["rationale"], str) and result["rationale"]


def test_cache_analysis_suggested_false_for_no_shared_prefix():
    a = "Translate the following text into French."
    b = "Summarize the following article in one paragraph."
    result = cache_analysis(a, b)

    assert result["suggested"] is False
    assert result["shared_prefix_chars"] == 0
    assert result["shared_prefix_ratio"] == 0.0


def test_cache_analysis_suggested_false_when_prefix_too_short():
    # Shares only "You " which is below the minimum char threshold.
    a = "You start here with completely different content A."
    b = "You go elsewhere with completely different content B."
    result = cache_analysis(a, b)
    assert result["shared_prefix_chars"] < 20
    assert result["suggested"] is False


def test_cache_analysis_full_prefix_containment():
    a = "You are a helpful assistant."
    b = "You are a helpful assistant. And you also do more."
    result = cache_analysis(a, b)

    assert result["shared_prefix_chars"] == len(a)
    assert result["shared_prefix_ratio"] == 1.0
    assert "already share" in result["rationale"] or "full prefix" in result["rationale"]


def test_cache_analysis_empty_inputs():
    result = cache_analysis("", "")
    assert result["shared_prefix_chars"] == 0
    assert result["shared_prefix_ratio"] == 0.0
    assert result["suggested"] is False


# ---- prefix_cache_analysis (per-prompt input-placement) ----

_BIG_STATIC = "You are a meticulous support assistant. " * 120  # ~4800 chars


def test_prefix_cache_recommends_moving_early_input_to_end():
    # Input at the very top, then a large static block -> reordering is a big win.
    content = "Question: {{question}}\n\n" + _BIG_STATIC + "Cite your sources."
    r = prefix_cache_analysis(content, avg_input_tokens=1800)
    assert r["recommendation"] == "move_inputs_to_end"
    assert r["meets_threshold"] is True
    assert r["first_variable"] == "question"
    # Almost nothing is cacheable now; almost everything could be.
    assert r["cacheable_prefix_tokens"] < 5
    assert r["reorder_gain_tokens"] > 500


def test_prefix_cache_optimal_when_input_last():
    content = _BIG_STATIC + "\n\nQuestion: {{question}}"
    r = prefix_cache_analysis(content, avg_input_tokens=1800)
    assert r["recommendation"] == "already_optimal"
    assert r["reorder_gain_tokens"] == 0


def test_prefix_cache_too_small_below_threshold():
    # A short prompt whose rendered requests never reach the cache floor.
    content = "Route the message to one of: {{handlers}}.\nMessage: {{message}}"
    r = prefix_cache_analysis(content, avg_input_tokens=380)
    assert r["recommendation"] == "too_small"
    assert r["meets_threshold"] is False


def test_prefix_cache_ignores_negligible_early_variable():
    # A tiny {{n}} up front is not worth a reorder nag when the bulky input is
    # already last and the static block is small.
    content = "Summarize into {{n}} bullets, faithfully.\n\n{{document}}"
    r = prefix_cache_analysis(content, avg_input_tokens=6400)
    assert r["recommendation"] == "already_optimal"


def test_prefix_cache_no_variables():
    r = prefix_cache_analysis("A fully static system prompt with no inputs.", avg_input_tokens=2000)
    assert r["recommendation"] == "no_variables"
    assert r["variables"] == []


def test_prefix_cache_segments_partition_content():
    content = "Hello {{name}}, your code is ${code} ok."
    r = prefix_cache_analysis(content)
    # Segments must reconstruct the original content exactly and tag vars.
    assert "".join(s["text"] for s in r["segments"]) == content
    assert [s["name"] for s in r["segments"] if s["type"] == "var"] == ["name", "code"]
    assert r["variables"] == ["name", "code"]


def test_prefix_cache_uses_template_estimate_without_telemetry():
    # No avg_input_tokens -> threshold falls back to the template length estimate.
    small = prefix_cache_analysis("Answer {{q}} briefly.")
    assert small["meets_threshold"] is False  # ~few tokens, well under the floor

    result = cache_analysis("something", "")
    assert result["shared_prefix_chars"] == 0
    assert result["shared_prefix_ratio"] == 0.0
    assert result["suggested"] is False


# ---- shorten_analysis ----


def _kinds(result) -> list[str]:
    return [f["kind"] for f in result["findings"]]


def test_shorten_flags_exact_duplicate_sentence():
    content = "Always cite your sources. Keep it brief. Always cite your sources."
    r = shorten_analysis(content, semantic=False)
    dups = [f for f in r["findings"] if f["kind"] == "duplicate"]
    assert len(dups) == 1
    assert "cite your sources" in dups[0]["text"].lower()
    assert dups[0]["tokens"] > 0


def test_shorten_flags_near_duplicate_sentence():
    # High token-set overlap but not textually identical -> near-duplicate.
    content = (
        "Please answer the question using only the provided context. "
        "Answer the question using only the provided context please."
    )
    r = shorten_analysis(content, semantic=False)
    assert "duplicate" in _kinds(r)


def test_shorten_flags_filler_phrases():
    content = "Please summarize this. It is important that you are accurate."
    r = shorten_analysis(content, semantic=False)
    filler = [f for f in r["findings"] if f["kind"] == "filler"]
    texts = " ".join(f["text"].lower() for f in filler)
    assert "please " in texts
    assert "it is important that " in texts


def test_shorten_flags_redundant_format_keyword():
    content = "Return JSON. Output must be JSON. Only JSON, nothing else, as JSON."
    r = shorten_analysis(content, semantic=False)
    fmt = [f for f in r["findings"] if f["kind"] == "redundant_format"]
    assert len(fmt) == 1
    assert "JSON" in fmt[0]["message"]
    # 4 mentions -> ~3 * est(json) tokens saved.
    assert fmt[0]["tokens"] == 3


def test_shorten_flags_excess_whitespace():
    content = "First line.\n\n\n\nSecond line."
    r = shorten_analysis(content, semantic=False)
    assert "whitespace" in _kinds(r)


def test_shorten_ignores_text_inside_variables():
    # 'json' occurs 3x but only ever as a VARIABLE slot; the static text has no
    # copies of it, so no redundant_format (or any) finding fires.
    content = "Output: {{json}} then {{json}} then {{json}}"
    r = shorten_analysis(content, semantic=False)
    assert r["findings"] == []
    assert r["est_tokens_saved"] == 0


def test_shorten_semantic_skipped_when_disabled():
    content = "Be concise. Be brief and to the point."
    r = shorten_analysis(content, semantic=False)
    assert r["semantic_available"] is False
    assert "semantic_duplicate" not in _kinds(r)


def test_shorten_semantic_skipped_when_embeddings_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "promptry.embeddings":
            raise ImportError("no embeddings extra")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    r = shorten_analysis("Cite your sources. Cite your sources.", semantic=True)
    assert r["semantic_available"] is False
    # Deterministic tier still runs.
    assert "duplicate" in _kinds(r)


def test_shorten_savings_sum_and_cap():
    content = "Please cite sources. Please cite sources. Please cite sources."
    r = shorten_analysis(content, semantic=False)
    # Savings never exceed the static token total, and are the deduped-by-text sum.
    assert r["est_tokens_saved"] <= r["total_tokens"]
    by_text = {}
    for f in r["findings"]:
        by_text[f["text"]] = f["tokens"]
    assert r["est_tokens_saved"] == min(r["total_tokens"], sum(by_text.values()))


def test_shorten_findings_sorted_by_tokens_desc():
    content = (
        "Please do this. "
        "This is a much longer redundant instruction sentence that repeats. "
        "This is a much longer redundant instruction sentence that repeats."
    )
    r = shorten_analysis(content, semantic=False)
    toks = [f["tokens"] for f in r["findings"]]
    assert toks == sorted(toks, reverse=True)
