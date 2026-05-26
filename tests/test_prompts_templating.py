"""Regression tests for the value-driven templating engine (promptry.prompts).

These lock in the behavior the dashboard, CLI, and SmartDoc all depend on:
substitution is *value-driven* — only supplied variable names are filled, in any
of {{x}} / {x} / ${x} / $x — so JSON braces and literal `$` are never mistaken
for variables. normalize_template canonicalizes the unambiguous forms to {{}}.
"""
from __future__ import annotations

from promptry.prompts import _substitute, normalize_template


class TestSubstitute:
    def test_double_brace(self):
        assert _substitute("Hi {{name}}", {"name": "Sam"}) == "Hi Sam"

    def test_single_brace(self):
        assert _substitute("Hi {name}", {"name": "Sam"}) == "Hi Sam"

    def test_dollar_and_braced_dollar(self):
        assert _substitute("$a and ${b}", {"a": "1", "b": "2"}) == "1 and 2"

    def test_mixed_styles(self):
        assert _substitute("{{a}} {b} ${c} $d", {"a": "1", "b": "2", "c": "3", "d": "4"}) == "1 2 3 4"

    def test_only_supplied_names_substituted(self):
        assert _substitute("{{a}} {{b}}", {"a": "X"}) == "X {{b}}"

    def test_json_braces_untouched(self):
        out = _substitute('Use {plan}. Return {"answer": x, "sources": []}', {"plan": "pro"})
        assert out == 'Use pro. Return {"answer": x, "sources": []}'

    def test_literal_dollar_amount_preserved(self):
        assert _substitute("costs $5 and $5K", {"name": "x"}) == "costs $5 and $5K"

    def test_unknown_placeholders_intact(self):
        assert _substitute("{{a}} $b {c}", {"a": "1"}) == "1 $b {c}"

    def test_whitespace_inside_braces(self):
        assert _substitute("{{ name }} and { plan }", {"name": "A", "plan": "B"}) == "A and B"

    def test_no_variables_returns_template_unchanged(self):
        assert _substitute("{{a}} $b {c}", {}) == "{{a}} $b {c}"

    def test_repeated_variable(self):
        assert _substitute("{{x}}-{{x}}", {"x": "7"}) == "7-7"

    def test_value_inserted_literally(self):
        # A replacement that itself contains $/backref/braces must not be
        # re-interpreted or re-scanned.
        assert _substitute("{{a}}", {"a": r"$b \1 {{c}}"}) == r"$b \1 {{c}}"

    def test_dollar_respects_word_boundary(self):
        assert _substitute("$nameX", {"name": "Z"}) == "$nameX"

    def test_longest_name_wins(self):
        assert _substitute("{{context_full}}", {"context": "A", "context_full": "B"}) == "B"

    def test_numeric_value_coerced(self):
        assert _substitute("n={{n}}", {"n": 42}) == "n=42"


class TestNormalizeTemplate:
    def test_dollar_to_brace(self):
        assert normalize_template("$role and ${task}") == "{{role}} and {{task}}"

    def test_existing_brace_passthrough(self):
        assert normalize_template("{{x}}") == "{{x}}"

    def test_json_untouched(self):
        assert normalize_template('{"k": 1} and $x') == '{"k": 1} and {{x}}'

    def test_single_brace_only_with_known_vars(self):
        assert normalize_template("Answer {q}", known_vars=["q"]) == "Answer {{q}}"

    def test_single_brace_left_alone_without_known_vars(self):
        # Ambiguous vs JSON/code, so untouched unless the var set is known.
        assert normalize_template("Answer {q}") == "Answer {q}"

    def test_no_double_wrap(self):
        assert normalize_template("{{x}}", known_vars=["x"]) == "{{x}}"

    def test_literal_dollar_amount(self):
        assert normalize_template("costs $5") == "costs $5"

    def test_empty(self):
        assert normalize_template("") == ""

    def test_idempotent(self):
        once = normalize_template("$a {b}", known_vars=["a", "b"])
        assert normalize_template(once, known_vars=["a", "b"]) == once == "{{a}} {{b}}"
