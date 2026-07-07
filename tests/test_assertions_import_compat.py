"""Import-compatibility regression test for Task 22 (assertions package split).

``promptry/assertions.py`` was split into a package
(``promptry/assertions/{__init__,judge,json_utils,text,tools,conversation}.py``).
This pins the exact set of public names that were importable from
``promptry.assertions`` *before* the split (captured via
``python -c "import promptry.assertions as a; print([n for n in dir(a) if
not n.startswith('__')])"`` against the pre-split module) and asserts every
one of them is still importable, unchanged, from the new package.
"""
from __future__ import annotations

import promptry.assertions as assertions

# Names importable from the flat `promptry/assertions.py` module, captured
# before the Task 22 package split. Do not "clean up" this list to match
# current internals -- it exists specifically to catch regressions in
# back-compat surface.
PRE_SPLIT_PUBLIC_NAMES = [
    "Any",
    "AssertionResult",
    "BaseModel",
    "Callable",
    "TYPE_CHECKING",
    "Type",
    "ValidationError",
    "_GRADING_PROMPT",
    "_GROUNDING_PROMPT",
    "_args_match",
    "_assertions_lock",
    "_cosine_similarity",
    "_embeddings_module",
    "_encode",
    "_get_model",
    "_judge",
    "_judge_cost_details",
    "_kwargs_match",
    "_lcs_length",
    "_levenshtein_distance",
    "_normalize_tool_call",
    "_normalize_trace",
    "_parse_grounding_output",
    "_parse_judge_output",
    "_run_predicate",
    "annotations",
    "append_result",
    "assert_all_assistant_turns",
    "assert_any_assistant_turn",
    "assert_contains",
    "assert_conversation_coherent",
    "assert_conversation_length",
    "assert_embedding_distance",
    "assert_exact",
    "assert_grounded",
    "assert_json_valid",
    "assert_levenshtein",
    "assert_llm",
    "assert_matches",
    "assert_no_repetition",
    "assert_no_tool_called",
    "assert_not_contains",
    "assert_rouge_l",
    "assert_schema",
    "assert_semantic",
    "assert_tool_called",
    "assert_tool_sequence",
    "clean_json",
    "get_judge",
    "json",
    "re",
    "set_judge",
    "set_model",
    "threading",
    "time",
]


def test_all_pre_split_names_still_importable_from_package():
    missing = [n for n in PRE_SPLIT_PUBLIC_NAMES if not hasattr(assertions, n)]
    assert not missing, f"names dropped by the assertions package split: {missing}"


def test_all_pre_split_names_importable_via_from_import():
    # `from promptry.assertions import <name>` must work for every name,
    # not just attribute access on the package object.
    ns: dict = {}
    exec(
        f"from promptry.assertions import {', '.join(PRE_SPLIT_PUBLIC_NAMES)}",
        ns,
    )
    for name in PRE_SPLIT_PUBLIC_NAMES:
        assert name in ns


def test_judge_state_is_single_instance():
    """set_judge()/get_judge() and direct `_judge`/`_assertions_lock`
    attribute access on the package must observe the same underlying state
    -- promptry.llm.get_default_judge() reads via
    `getattr(promptry.assertions, "_judge", None)`, and several tests reset
    state via direct `assertions._judge = ...` assignment."""
    from promptry import llm

    previous = assertions.get_judge()
    try:
        def fn(prompt: str) -> str:
            return "canary"

        assertions.set_judge(fn)
        assert llm.get_default_judge() is fn

        with assertions._assertions_lock:
            assertions._judge = None
        assert assertions.get_judge() is None

        with assertions._assertions_lock:
            assertions._judge = fn
        assert assertions.get_judge() is fn
    finally:
        with assertions._assertions_lock:
            assertions._judge = previous
