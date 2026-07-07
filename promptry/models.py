"""Data classes used across promptry.

All the shapes that get passed between modules live here so nothing
has to import from storage or runner just to get a type.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---- prompt records ----

@dataclass
class PromptRecord:
    id: int
    name: str
    version: int
    content: str
    hash: str
    metadata: dict
    created_at: str
    tags: list[str] = field(default_factory=list)


# ---- eval records ----

@dataclass
class EvalRunRecord:
    id: int
    suite_name: str
    prompt_name: str | None
    prompt_version: int | None
    model_version: str | None
    timestamp: str
    overall_pass: bool
    overall_score: float | None


@dataclass
class EvalResultRecord:
    id: int
    run_id: int
    test_name: str
    assertion_type: str
    passed: bool
    score: float | None
    details: dict | None
    latency_ms: float | None


# ---- runner / suite results ----

@dataclass
class TestResult:
    # Keep pytest from trying to collect this as a test class.
    __test__ = False

    test_name: str
    passed: bool
    assertions: list  # list[AssertionResult] -- avoids circular import
    error: str | None = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        """Canonical dict shape consumed by report.py and the dashboard.

        Assertions are read via getattr rather than an import of
        AssertionResult (avoids the circular import noted above) and
        matches the tolerant access pattern the pre-refactor helper used.
        """
        assertions = []
        for a in self.assertions:
            assertions.append({
                "assertion_type": a.assertion_type,
                "passed": a.passed,
                "score": a.score,
                "details": getattr(a, "details", None),
            })
        return {
            "test_name": self.test_name,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "assertions": assertions,
        }


@dataclass
class SuiteResult:
    suite_name: str
    tests: list[TestResult]
    overall_pass: bool = True
    overall_score: float = 0.0
    prompt_name: str | None = None
    prompt_version: int | None = None
    model_version: str | None = None
    run_id: int | None = None

    def to_dict(self) -> dict:
        """Canonical dict shape for report rendering.

        Note: prompt_name/prompt_version/model_version/run_id are
        intentionally NOT included -- this mirrors the pre-refactor
        ``cli._suite_result_to_dict`` shape exactly, which is the JSON
        contract consumed by report.py and the dashboard.
        """
        return {
            "suite_name": self.suite_name,
            "overall_pass": self.overall_pass,
            "overall_score": self.overall_score,
            "tests": [t.to_dict() for t in self.tests],
        }


# ---- comparison ----

@dataclass
class ComparisonResult:
    metric: str
    baseline_value: float
    current_value: float
    passed: bool

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "passed": self.passed,
        }


@dataclass
class RootCauseHint:
    """A possible explanation for a regression."""
    cause: str
    detail: str


# ---- drift ----

@dataclass
class DriftReport:
    """Drift detection report for a suite's score history.

    Fields:
        suite_name: the suite being analyzed
        window: max runs requested (config.monitor.window)
        scores: the actual score values analyzed, oldest-first
        slope: OLS linear regression slope. Negative = scores trending down.
        mean_score: mean of the window
        stddev_score: sample standard deviation of the window
        latest_score: most recent score
        latest_z: z-score of latest vs window (None if stddev is zero)
        p_value: Mann-Whitney U p-value comparing recent half vs older half of
            the window. None if fewer than 16 runs (too small for the normal
            approximation). Lower p = more evidence the recent half differs.
        is_drifting: True iff slope is steeper than -threshold. Preserved for
            backward compat; see `confidence` for a richer signal.
        threshold: slope threshold used for is_drifting
        confidence: one of "high" | "medium" | "low" | "insufficient".
            Combines slope and statistical significance. See drift.py docs.
        message: human-readable summary
    """
    suite_name: str
    window: int
    scores: list[float]
    slope: float
    mean_score: float
    latest_score: float
    is_drifting: bool
    threshold: float
    message: str
    stddev_score: float = 0.0
    latest_z: float | None = None
    p_value: float | None = None
    confidence: str = "low"
