"""promptry - regression protection for LLM pipelines."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: the version declared in pyproject.toml and
    # baked into the installed package's metadata. Avoids the literal drifting
    # out of sync with what pip actually installed.
    __version__ = _pkg_version("promptry")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0+unknown"

from promptry.registry import track, track_context, track_invocation, vote, save_dataset, load_dataset, PromptRegistry
from promptry.prompts import seed_prompt, get_prompt_template, render_prompt
from promptry.feedback import analyze_votes
from promptry.evaluator import suite, check_all
from promptry.assertions import (
    assert_semantic,
    assert_exact,
    assert_levenshtein,
    assert_rouge_l,
    assert_embedding_distance,
    assert_contains,
    assert_not_contains,
    assert_schema,
    assert_llm,
    assert_json_valid,
    assert_matches,
    assert_grounded,
    assert_tool_called,
    assert_tool_sequence,
    assert_no_tool_called,
    assert_conversation_length,
    assert_all_assistant_turns,
    assert_any_assistant_turn,
    assert_conversation_coherent,
    assert_no_repetition,
    clean_json,
    set_judge,
)
from promptry.conversation import Conversation, Turn
from promptry.runner import run_suite
from promptry.drift import DriftMonitor
from promptry.templates import get_templates, run_safety_audit
from promptry.trajectory import (
    Trajectory,
    TrajectoryStep,
    TrajectoryDiff,
    analyze_trajectory,
    diff_trajectories,
    format_trajectory_diff,
    assert_trajectory_max_steps,
    assert_no_redundant_tool_calls,
    assert_tool_input_matches,
    assert_final_answer_present,
)
from promptry.capture import (
    Capture,
    CaptureRecorder,
    ReplayResult,
    get_recorder,
    default_recorder,
    capture_enabled,
    load_captures,
    iter_captures,
    replay_captures,
    redact_sensitive,
)
from promptry.clustering import (
    cluster_failures,
    format_clustering_report,
    ClusteringReport,
    FailureCluster,
)
from promptry.garak import (
    import_report as import_garak_report,
    parse_garak_jsonl,
    GarakEval,
    GarakRun,
)
from promptry.naming import task, trace
# Imported from the callback module directly (not promptry.integrations, which
# would pull litellm at import); enable_litellm() itself imports litellm lazily.
from promptry.integrations.litellm_callback import enable_litellm

__all__ = [
    "track",
    "track_context",
    "track_invocation",
    "task",
    "trace",
    "enable_litellm",
    "seed_prompt",
    "get_prompt_template",
    "render_prompt",
    "vote",
    "save_dataset",
    "load_dataset",
    "analyze_votes",
    "PromptRegistry",
    "suite",
    "check_all",
    "assert_semantic",
    "assert_exact",
    "assert_levenshtein",
    "assert_rouge_l",
    "assert_embedding_distance",
    "assert_contains",
    "assert_not_contains",
    "assert_schema",
    "assert_llm",
    "assert_json_valid",
    "assert_matches",
    "assert_grounded",
    "assert_tool_called",
    "assert_tool_sequence",
    "assert_no_tool_called",
    "assert_conversation_length",
    "assert_all_assistant_turns",
    "assert_any_assistant_turn",
    "assert_conversation_coherent",
    "assert_no_repetition",
    "Conversation",
    "Turn",
    "clean_json",
    "set_judge",
    "run_suite",
    "DriftMonitor",
    "get_templates",
    "run_safety_audit",
    "Trajectory",
    "TrajectoryStep",
    "TrajectoryDiff",
    "analyze_trajectory",
    "diff_trajectories",
    "format_trajectory_diff",
    "assert_trajectory_max_steps",
    "assert_no_redundant_tool_calls",
    "assert_tool_input_matches",
    "assert_final_answer_present",
    "Capture",
    "CaptureRecorder",
    "ReplayResult",
    "get_recorder",
    "default_recorder",
    "capture_enabled",
    "load_captures",
    "iter_captures",
    "replay_captures",
    "redact_sensitive",
    "cluster_failures",
    "format_clustering_report",
    "ClusteringReport",
    "FailureCluster",
    "import_garak_report",
    "parse_garak_jsonl",
    "GarakEval",
    "GarakRun",
]
