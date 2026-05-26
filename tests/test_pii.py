"""Regression tests for promptry.pii — the secret/PII tripwire over captured text."""
from __future__ import annotations

from promptry.pii import scan_text, scan_invocation, _luhn_ok


def _types(text):
    return {f.type for f in scan_text(text)}


class TestScanText:
    def test_openai_key(self):
        assert "openai_api_key" in _types("key sk-abcdefghij1234567890XYZ here")

    def test_anthropic_key(self):
        assert "anthropic_api_key" in _types("sk-ant-abcdefghij1234567890abcd")

    def test_private_key_header(self):
        assert "private_key" in _types("-----BEGIN RSA PRIVATE KEY-----\nMII...")

    def test_jwt(self):
        assert "jwt" in _types("token eyJhbG.eyJzdWI.SflKxwRJ")

    def test_email(self):
        assert "email" in _types("contact jane.doe@acme.com please")

    def test_us_ssn(self):
        assert "us_ssn" in _types("ssn 123-45-6789")

    def test_credit_card_luhn_valid(self):
        assert "credit_card" in _types("card 4111 1111 1111 1111")

    def test_non_luhn_digits_not_flagged_as_card(self):
        # 13-digit run that fails the Luhn check (e.g. an order id)
        assert "credit_card" not in _types("order 1234567890123")

    def test_clean_text_no_findings(self):
        assert scan_text("a perfectly normal helpful answer about cats") == []

    def test_empty(self):
        assert scan_text("") == []
        assert scan_text(None) == []

    def test_redaction_masks_value(self):
        findings = scan_text("key sk-abcdefghij1234567890WXYZ")
        f = next(x for x in findings if x.type == "openai_api_key")
        # sample must NOT contain the raw secret, but identifies it (last 4)
        assert "sk-abcdefghij" not in f.sample
        assert f.sample.endswith("WXYZ")
        assert "•" in f.sample  # masked with bullets

    def test_secret_vs_pii_category(self):
        secrets = {f.type: f.category for f in scan_text(
            "sk-abcdefghij1234567890XYZ and jane@acme.com")}
        assert secrets["openai_api_key"] == "secret"
        assert secrets["email"] == "pii"

    def test_counts_distinct_findings_of_a_type(self):
        # count is the number of distinct (masked) values seen for the type
        findings = scan_text("alice@acme.io bob@beta.dev carol@gamma.net")
        email = next(f for f in findings if f.type == "email")
        assert email.count == 3


class TestLuhn:
    def test_valid(self):
        assert _luhn_ok("4111111111111111")

    def test_invalid(self):
        assert not _luhn_ok("4111111111111112")

    def test_too_short(self):
        assert not _luhn_ok("411111")


class TestScanInvocation:
    def test_aggregates_both_sides(self):
        r = scan_invocation("sk-abcdefghij1234567890XYZ", "email jane@acme.com")
        assert r["total"] == 2
        assert r["has_secret"] is True
        assert r["worst_severity"] == "high"  # the api key
        assert len(r["input"]) == 1 and len(r["output"]) == 1

    def test_clean(self):
        r = scan_invocation("hello", "world")
        assert r["total"] == 0
        assert r["has_secret"] is False
        assert r["worst_severity"] is None
