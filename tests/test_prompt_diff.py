"""Tests for promptry.prompt_diff (cross-prompt diff + cache analysis)."""
from __future__ import annotations

from promptry.prompt_diff import cache_analysis, diff_prompts


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

    result = cache_analysis("something", "")
    assert result["shared_prefix_chars"] == 0
    assert result["shared_prefix_ratio"] == 0.0
    assert result["suggested"] is False
