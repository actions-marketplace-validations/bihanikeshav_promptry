"""Assertion functions for eval suites.

Each assertion:
  1. Evaluates the condition
  2. Appends an AssertionResult to the current run context
  3. Raises AssertionError on failure
  4. Returns the score (so you can use it if you want)

This is a package (judge lifecycle / json_utils / text / tools /
conversation submodules) that re-exports every name that used to be
importable from the single ``promptry/assertions.py`` module, so existing
``from promptry.assertions import X`` and ``import promptry.assertions as
assertions; assertions.X`` call sites keep working unchanged.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import types
from typing import Any, Callable, TYPE_CHECKING, Type

from pydantic import BaseModel, ValidationError

from promptry.evaluator import AssertionResult, append_result

if TYPE_CHECKING:
    from promptry.conversation import Conversation

# Embedding model access + cache now live in promptry.embeddings. These
# names stay here as re-exports for back-compat (several modules and
# possibly external callers import them from promptry.assertions).
from promptry.embeddings import get_embedder as _get_model, set_model, encode as _encode, cosine_similarity as _cosine_similarity
# Imported as a module (not `from ... import similarity`) so that tests can
# monkeypatch promptry.embeddings.similarity and have assert_embedding_distance
# pick up the replacement at call time.
from promptry import embeddings as _embeddings_module

from promptry.assertions import judge as _judge_module
from promptry.assertions.judge import (
    set_judge,
    get_judge,
    assert_llm,
    assert_grounded,
    g_eval,
    run_scored_judge,
    _GRADING_PROMPT,
    _GROUNDING_PROMPT,
    _parse_judge_output,
    _parse_grounding_output,
    _judge_cost_details,
)
from promptry.assertions.rag import (
    assert_answer_relevancy,
    assert_faithfulness,
    assert_context_precision,
    assert_context_recall,
)
from promptry.assertions.json_utils import (
    clean_json,
    assert_json_valid,
    assert_schema,
)
from promptry.assertions.text import (
    assert_semantic,
    assert_exact,
    _levenshtein_distance,
    assert_levenshtein,
    _lcs_length,
    assert_rouge_l,
    assert_embedding_distance,
    assert_contains,
    assert_not_contains,
    assert_matches,
)
from promptry.assertions.tools import (
    _normalize_tool_call,
    _normalize_trace,
    _args_match,
    _kwargs_match,
    assert_tool_called,
    assert_tool_sequence,
    assert_no_tool_called,
)
from promptry.assertions.conversation import (
    assert_conversation_length,
    _run_predicate,
    assert_all_assistant_turns,
    assert_any_assistant_turn,
    assert_conversation_coherent,
    assert_no_repetition,
)


class _AssertionsModule(types.ModuleType):
    """Module subclass so ``_judge``/``_assertions_lock`` on this package are
    live aliases for the single instances owned by ``promptry.assertions.judge``.

    A plain ``from .judge import _judge`` would copy the reference at import
    time; `judge.set_judge`/`judge.get_judge` would then mutate a name in
    ``judge``'s own namespace that this package's copy never sees. External
    code (tests, ``promptry.llm.get_default_judge``) reads and writes
    ``promptry.assertions._judge``/``_assertions_lock`` directly and expects
    single-instance semantics, so these are exposed as module-level
    properties that forward to ``judge`` instead.
    """

    @property
    def _judge(self):
        return _judge_module._judge

    @_judge.setter
    def _judge(self, value):
        _judge_module._judge = value

    @property
    def _assertions_lock(self):
        return _judge_module._assertions_lock

    def __dir__(self):
        # ModuleType.__dir__ only lists self.__dict__ keys, which would
        # otherwise hide the two class-level properties above.
        return sorted(set(super().__dir__()) | {"_judge", "_assertions_lock"})


sys.modules[__name__].__class__ = _AssertionsModule
