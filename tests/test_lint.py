"""Direct unit tests for promptry.lint.

Covers the two core functions:
  - extract_variables (~line 21): placeholder discovery, first-seen order.
  - lint_prompt (~line 42): the rule engine that returns structured findings.

Each lint-rule test asserts a SPECIFIC rule fires (or does not) on a
SPECIFIC input, so a regression in any single rule fails a test.
"""
from promptry.lint import extract_variables, lint_prompt


def _levels(findings):
    return [f["level"] for f in findings]


def _joined(findings):
    return " || ".join(f["message"] for f in findings)


class TestExtractVariables:

    def test_brace_syntax(self):
        assert extract_variables("Hello {{name}}, welcome to {{place}}") == ["name", "place"]

    def test_dollar_and_braced_dollar(self):
        assert extract_variables("$foo and ${bar} here") == ["foo", "bar"]

    def test_first_seen_order_preserved(self):
        # 'b' first, then 'a', then 'b' again -> dedup keeps first-seen order.
        assert extract_variables("{{b}} {{a}} {{b}}") == ["b", "a"]

    def test_dedup_across_syntaxes(self):
        # same name via {{name}} and $name should collapse to one entry.
        assert extract_variables("{{name}} then $name") == ["name"]

    def test_mixed_syntax_order(self):
        assert extract_variables("start {{one}} mid ${two} end $three") == ["one", "two", "three"]

    def test_no_placeholders(self):
        assert extract_variables("plain text no vars") == []

    def test_empty_and_none(self):
        assert extract_variables("") == []
        assert extract_variables(None) == []

    def test_escaped_dollar_is_not_a_variable(self):
        # $$ is an escape, not a placeholder; the digits after aren't a valid name.
        assert extract_variables("price is $$5 today") == []


class TestLintEmpty:

    def test_empty_string_is_error(self):
        findings = lint_prompt("")
        assert len(findings) == 1
        assert findings[0]["level"] == "error"
        assert "empty" in findings[0]["message"].lower()

    def test_whitespace_only_is_error(self):
        findings = lint_prompt("   \n\t  ")
        assert _levels(findings) == ["error"]


class TestStrayDollarRule:

    def test_stray_dollar_fires_warning(self):
        findings = lint_prompt("The cost is $5 per unit")
        stray = [f for f in findings if "stray" in f["message"]]
        assert len(stray) == 1
        assert stray[0]["level"] == "warning"
        assert "1 stray" in stray[0]["message"]

    def test_valid_placeholder_does_not_fire_stray(self):
        findings = lint_prompt("Hello $name, your total is fine")
        assert not any("stray" in f["message"] for f in findings)

    def test_escaped_double_dollar_does_not_fire_stray(self):
        findings = lint_prompt("The price is $$5 and $$10")
        assert not any("stray" in f["message"] for f in findings)

    def test_multiple_stray_dollars_counted(self):
        findings = lint_prompt("$5 and $6 and $7 raw numbers")
        stray = [f for f in findings if "stray" in f["message"]]
        assert len(stray) == 1
        assert "3 stray" in stray[0]["message"]


class TestJsonGuidanceRule:

    def test_json_without_only_fires_info(self):
        findings = lint_prompt("Respond in JSON format please")
        json_findings = [f for f in findings if "JSON" in f["message"] and f["level"] == "info"]
        assert len(json_findings) == 1

    def test_json_with_only_does_not_fire(self):
        findings = lint_prompt("Respond in JSON only, no prose")
        assert not any("valid JSON" in f["message"] for f in findings)

    def test_json_with_valid_json_phrase_does_not_fire(self):
        findings = lint_prompt("Return valid JSON describing the result")
        assert not any(f["level"] == "info" and "wrap output" in f["message"] for f in findings)

    def test_no_json_mention_does_not_fire(self):
        findings = lint_prompt("Summarize the document in one paragraph.")
        assert not any("JSON" in f["message"] for f in findings)


class TestKnownVarsRule:

    def test_unknown_placeholder_flagged(self):
        findings = lint_prompt("Hello {{name}} and {{age}}", known_vars=["name"])
        warns = [f for f in findings if f["level"] == "warning" and "placeholders the caller" in f["message"]]
        assert len(warns) == 1
        assert "age" in warns[0]["message"]
        assert "name" not in warns[0]["message"].split(":")[-1]

    def test_all_known_does_not_flag(self):
        findings = lint_prompt("Hello {{name}} and {{age}}", known_vars=["name", "age"])
        assert not any("placeholders the caller" in f["message"] for f in findings)

    def test_known_vars_none_skips_rule(self):
        # known_vars=None (the default) must not produce the extras warning at all.
        findings = lint_prompt("Hello {{name}} and {{age}}", known_vars=None)
        assert not any("placeholders the caller" in f["message"] for f in findings)


class TestLongSingleLineRule:

    def test_long_single_line_fires_info(self):
        template = "word " * 200  # >600 chars, no newline
        assert len(template) > 600 and "\n" not in template
        findings = lint_prompt(template)
        assert any(f["level"] == "info" and "single-line" in f["message"] for f in findings)

    def test_long_but_multiline_does_not_fire(self):
        template = ("word " * 100) + "\n" + ("word " * 100)
        assert len(template) > 600
        findings = lint_prompt(template)
        assert not any("single-line" in f["message"] for f in findings)

    def test_short_single_line_does_not_fire(self):
        findings = lint_prompt("Short single-line prompt.")
        assert not any("single-line" in f["message"] for f in findings)


class TestCleanPrompt:

    def test_clean_prompt_has_no_findings(self):
        findings = lint_prompt("You are a helpful assistant. Answer {{question}} clearly.")
        assert findings == []
