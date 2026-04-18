"""Failure clustering (v0.9 C2).

After a suite runs many times, you accumulate a pile of failing assertions.
cluster_failures() groups them so you can see *patterns* instead of a flat
list — "47 failures in the last week; 23 are about dates before 2023, 18
are about trailing periods, 6 are miscellaneous."

Two similarity modes:
- Semantic (default when sentence-transformers is installed): embed each
  failure signature, greedy-cluster by cosine similarity.
- String (fallback): cluster by normalized-string prefix match. No
  external dependency needed.

This module never hits the network. All reads go through the local
storage singleton.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Signature extraction — a short string summarizing a single failure
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+\.?\d*")
_WS_RE = re.compile(r"\s+")


def failure_signature(result) -> str:
    """Build a short string key for a failed EvalResultRecord-ish object.

    Strategy:
    - Use the assertion-specific detail fields when they exist (missing
      keywords, regex miss, etc.)
    - Fall back to the details['error'] or details['reason'] field.
    - Strip numeric values so "score 0.71 < 0.8" and "score 0.43 < 0.8"
      cluster together.

    The passed-in object may be an EvalResultRecord, a dict, or anything
    with `.assertion_type` and `.details` attributes.
    """
    atype = _attr(result, "assertion_type", "")
    details = _attr(result, "details", {}) or {}

    if atype == "contains":
        missing = details.get("missing") or details.get("missing_keywords") or []
        if missing:
            return f"contains: missing {sorted(set(missing))[:5]}"
    if atype == "matches":
        pat = details.get("pattern") or ""
        return f"matches: pattern={pat!r}"
    if atype == "semantic":
        thr = details.get("threshold")
        return f"semantic: below threshold={thr}"
    if atype == "llm":
        reason = details.get("reason") or details.get("error") or ""
        return f"llm: {_normalize(reason)[:120]}"
    if atype == "grounded":
        fab = details.get("fabricated") or []
        n = details.get("fabricated_count") or len(fab)
        return f"grounded: {n} unsupported claim(s)"
    if atype == "schema":
        errs = details.get("errors") or details.get("error") or ""
        return f"schema: {_normalize(str(errs))[:120]}"
    if atype.startswith("tool"):
        name = details.get("tool_name") or details.get("expected_sequence")
        return f"{atype}: {name}"

    # generic: use reason or error message
    reason = details.get("reason") or details.get("error") or ""
    return f"{atype}: {_normalize(str(reason))[:120]}"


def _attr(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize(text: str) -> str:
    """Collapse whitespace and blank out numbers so that messages differing
    only in numeric values hash to the same signature."""
    text = _NUMBER_RE.sub("N", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Cluster data structures
# ---------------------------------------------------------------------------

@dataclass
class FailureCluster:
    representative: str
    size: int
    examples: list[dict] = field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    test_names: list[str] = field(default_factory=list)
    assertion_types: list[str] = field(default_factory=list)


@dataclass
class ClusteringReport:
    suite_name: str
    days: int
    total_failures: int
    clusters: list[FailureCluster]
    mode: str  # "semantic" | "string"

    def to_dict(self) -> dict:
        return {
            "suite_name": self.suite_name,
            "days": self.days,
            "total_failures": self.total_failures,
            "mode": self.mode,
            "clusters": [
                {
                    "representative": c.representative,
                    "size": c.size,
                    "examples": c.examples,
                    "first_seen": c.first_seen,
                    "last_seen": c.last_seen,
                    "test_names": c.test_names,
                    "assertion_types": c.assertion_types,
                }
                for c in self.clusters
            ],
        }


# ---------------------------------------------------------------------------
# Clustering core
# ---------------------------------------------------------------------------

def cluster_failures(
    suite_name: str,
    *,
    days: int = 7,
    min_cluster_size: int = 2,
    similarity_threshold: float = 0.75,
    mode: str = "auto",
    storage=None,
    max_failures: int = 500,
) -> ClusteringReport:
    """Cluster recent failed assertion results for a suite.

    Args:
        suite_name: the suite to analyze.
        days: look-back window in days.
        min_cluster_size: clusters smaller than this are excluded from the
            report (still counted in total_failures).
        similarity_threshold: cosine threshold for semantic mode. Higher =
            tighter clusters. 0.75 is a reasonable default for short
            failure strings.
        mode: "auto" picks semantic if sentence-transformers is importable,
            else string. Pass "semantic" or "string" to force.
        storage: optional BaseStorage override; defaults to singleton.
        max_failures: cap on failures pulled from storage (protects against
            runaway analysis on huge projects).

    Returns a ClusteringReport with clusters sorted by size descending.
    """
    if storage is None:
        from promptry.storage import get_storage
        storage = get_storage()

    failures = _collect_failures(storage, suite_name, days, max_failures)
    resolved_mode = _resolve_mode(mode)
    if not failures:
        return ClusteringReport(
            suite_name=suite_name,
            days=days,
            total_failures=0,
            clusters=[],
            mode=resolved_mode,
        )

    sig_rows = [(failure_signature(f), f) for f in failures]

    if resolved_mode == "semantic":
        clusters_raw = _cluster_semantic(sig_rows, similarity_threshold)
    else:
        clusters_raw = _cluster_string(sig_rows)

    clusters: list[FailureCluster] = []
    for rep, members in clusters_raw:
        if len(members) < min_cluster_size:
            continue
        ts_values = [_attr_ts(m) for m in members]
        ts_values = [t for t in ts_values if t]
        clusters.append(FailureCluster(
            representative=rep,
            size=len(members),
            examples=[_example_of(m) for m in members[:3]],
            first_seen=min(ts_values) if ts_values else None,
            last_seen=max(ts_values) if ts_values else None,
            test_names=_uniq([_attr(m, "test_name", "") for m in members]),
            assertion_types=_uniq([_attr(m, "assertion_type", "") for m in members]),
        ))

    clusters.sort(key=lambda c: c.size, reverse=True)

    return ClusteringReport(
        suite_name=suite_name,
        days=days,
        total_failures=len(failures),
        clusters=clusters,
        mode=resolved_mode,
    )


def _collect_failures(storage, suite_name: str, days: int, limit: int) -> list:
    """Pull failed eval results for a suite in the recent window.

    The existing storage API lets us get runs and then results-per-run. We
    don't have a direct "failures for suite X in last N days" query, so we
    paginate runs and filter.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    runs = storage.get_eval_runs(suite_name, limit=200)
    failures: list = []
    for run in runs:
        # best-effort timestamp parse; skip runs outside the window
        ts_raw = getattr(run, "timestamp", None) or getattr(run, "created_at", None)
        if ts_raw:
            parsed = _parse_iso(ts_raw)
            if parsed and parsed < cutoff:
                continue
        run_id = getattr(run, "id", None)
        if run_id is None:
            continue
        try:
            results = storage.get_eval_results(run_id)
        except Exception:
            continue
        for r in results:
            if _attr(r, "passed", True):
                continue
            failures.append(r)
            if len(failures) >= limit:
                return failures
    return failures


def _parse_iso(s: str) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        # handle 'Z' suffix
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _attr_ts(obj) -> str | None:
    """Best-effort timestamp for sorting. Prefers run_at on the result,
    falls back to None — callers handle None."""
    return _attr(obj, "created_at") or _attr(obj, "timestamp")


def _example_of(r) -> dict:
    return {
        "test_name": _attr(r, "test_name", ""),
        "assertion_type": _attr(r, "assertion_type", ""),
        "signature": failure_signature(r),
    }


def _uniq(xs):
    seen = []
    for x in xs:
        if x and x not in seen:
            seen.append(x)
    return seen


# ---------------------------------------------------------------------------
# String-mode clustering (no deps)
# ---------------------------------------------------------------------------

def _cluster_string(rows: list[tuple[str, object]]) -> list[tuple[str, list]]:
    """Cluster by exact normalized-signature match."""
    buckets: dict[str, list] = {}
    for sig, obj in rows:
        buckets.setdefault(sig, []).append(obj)
    return [(rep, members) for rep, members in buckets.items()]


# ---------------------------------------------------------------------------
# Semantic-mode clustering (needs sentence-transformers)
# ---------------------------------------------------------------------------

def _cluster_semantic(
    rows: list[tuple[str, object]],
    threshold: float,
) -> list[tuple[str, list]]:
    """Greedy online clustering: embed each signature; place into the
    highest-similarity existing cluster if above threshold, else new.
    """
    try:
        from promptry.assertions import _get_model
        model = _get_model()
        from sentence_transformers.util import cos_sim
    except Exception:
        # fallback to string mode
        return _cluster_string(rows)

    if not rows:
        return []

    sigs = [s for s, _ in rows]
    embeddings = model.encode(sigs, convert_to_tensor=True)

    # Track cluster centroids as the FIRST member's embedding.
    cluster_centroids: list = []
    cluster_members: list[list] = []
    cluster_reps: list[str] = []

    for i, (sig, obj) in enumerate(rows):
        emb = embeddings[i]
        best_idx = -1
        best_sim = -1.0
        for ci, centroid in enumerate(cluster_centroids):
            sim = float(cos_sim(emb, centroid)[0][0])
            if sim > best_sim:
                best_sim = sim
                best_idx = ci
        if best_idx != -1 and best_sim >= threshold:
            cluster_members[best_idx].append(obj)
        else:
            cluster_centroids.append(emb)
            cluster_members.append([obj])
            cluster_reps.append(sig)

    return list(zip(cluster_reps, cluster_members))


def _resolve_mode(mode: str) -> str:
    if mode == "string":
        return "string"
    if mode == "semantic":
        return "semantic"
    # auto
    try:
        import sentence_transformers  # noqa: F401
        return "semantic"
    except ImportError:
        return "string"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def format_clustering_report(report: ClusteringReport, top_n: int = 5) -> str:
    """Render a ClusteringReport as terminal-friendly text."""
    if report.total_failures == 0:
        return f"No failures in the last {report.days} day(s) for '{report.suite_name}'."

    lines = [
        f"{report.suite_name}: {report.total_failures} failure(s) in last {report.days}d",
        f"Mode: {report.mode}, clusters: {len(report.clusters)}",
        "",
    ]
    for i, c in enumerate(report.clusters[:top_n]):
        share = (c.size / report.total_failures) * 100 if report.total_failures else 0
        lines.append(f"[{i+1}] {c.size}x ({share:.0f}%)  {c.representative}")
        if c.first_seen or c.last_seen:
            lines.append(f"    window: {c.first_seen or '?'} -> {c.last_seen or '?'}")
        if c.test_names:
            preview = ", ".join(c.test_names[:3])
            more = f" (+{len(c.test_names) - 3} more)" if len(c.test_names) > 3 else ""
            lines.append(f"    tests: {preview}{more}")
        lines.append("")

    remainder = len(report.clusters) - top_n
    if remainder > 0:
        lines.append(f"... plus {remainder} smaller cluster(s)")
    return "\n".join(lines)
