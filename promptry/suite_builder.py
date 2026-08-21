"""Assemble and persist declarative ``evals.yaml`` suites.

Backs the in-dashboard "suite creator": a user assembles eval cases from
manual entries, promoted golden examples, and positive-feedback invocations,
and this module writes them out as a declarative suite that
:func:`promptry.yaml_suites.load_yaml_suites` can load unchanged.

Two public entry points:

* :func:`write_yaml_suite` -- append/replace a suite in an ``evals.yaml`` file
  (the single write path shared with the ``promptry new suite`` wizard).
* :func:`suite_candidates` -- source ready-to-edit cases from stored golden
  examples or from invocations that received positive end-user feedback.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Untrusted-surface guards (HTTP dashboard + MCP).
#
# The dashboard and MCP server are the network-reachable surface; hand-authored
# evals.yaml files and the CLI are trusted authorship. These helpers enforce
# that boundary so a suite created over HTTP/MCP can neither run arbitrary code
# (`pipeline`) nor write/read files outside the project tree.
# ---------------------------------------------------------------------------

class SuiteInputError(ValueError):
    """A suite create/edit request that fails a security or shape check."""


def api_pipeline_allowed() -> bool:
    """Whether pipeline suites may be created over the API/MCP (opt-in)."""
    return os.environ.get("PROMPTRY_ALLOW_API_PIPELINE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def check_pipeline_allowed(pipeline) -> None:
    """Reject a ``pipeline`` spec arriving over an untrusted surface.

    A ``pipeline`` is an arbitrary ``"module:function"`` imported and executed
    when the suite later runs (in CI, the scheduler, or ``promptry run``), so
    accepting one from the network is remote code execution. Suites written by
    hand into ``evals.yaml`` are trusted and unaffected. Opt back in with
    ``PROMPTRY_ALLOW_API_PIPELINE=1``.
    """
    if pipeline and not api_pipeline_allowed():
        raise SuiteInputError(
            "pipeline suites cannot be created via the API/MCP because a "
            "pipeline runs arbitrary code when the suite runs. Add the suite "
            "to evals.yaml directly, or set PROMPTRY_ALLOW_API_PIPELINE=1 to "
            "allow it."
        )


def safe_suite_path(user_path, root, default_name: str = "evals.yaml") -> Path:
    """Confine a caller-supplied suite output/read path to the project tree.

    Rejects absolute paths, parent-directory escapes (resolved, so symlinks and
    ``..`` are covered), non-YAML suffixes, and any dot-prefixed path component
    (which would otherwise reach ``.git/hooks``, ``.github/workflows``, ``.env``
    and similar code-exec sinks). Returns the resolved path, or
    ``<root>/<default_name>`` when no path is supplied.
    """
    root = Path(root).resolve()
    if not user_path:
        return root / default_name
    p = Path(user_path)
    # Reject absolute paths under BOTH path flavours, not just the host's: on
    # POSIX, Path("C:\\Windows\\x") is a (bizarre) relative name and slips the
    # native is_absolute() check, so a drive-letter or UNC path would otherwise
    # be accepted on a Linux server. PureWindowsPath catches both.
    if p.is_absolute() or PureWindowsPath(user_path).is_absolute():
        raise SuiteInputError("output path must be relative to the project directory")
    if any(part.startswith(".") for part in p.parts):
        raise SuiteInputError(
            "output path must not contain dot-prefixed directories or files"
        )
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root):
        raise SuiteInputError("output path escapes the project directory")
    if resolved.suffix.lower() not in {".yaml", ".yml"}:
        raise SuiteInputError("output path must end in .yaml or .yml")
    return resolved


# ---------------------------------------------------------------------------
# Writing a suite into evals.yaml (the one shared write path).
# ---------------------------------------------------------------------------

def write_yaml_suite(path, suite: dict, overwrite: bool = False,
                     elsewhere_hint: str = "elsewhere") -> list[str]:
    """Append ``suite`` to the ``suites:`` list in the ``evals.yaml`` at ``path``.

    Creates the file (or replaces a comments-only/empty starter scaffold) when
    needed. The whole document is round-tripped through
    ``yaml.safe_load``/``yaml.safe_dump`` -- correct, but any comments or
    hand-formatting in a *non-empty* file are not preserved.

    If a suite of the same name already exists, it is replaced in place when
    ``overwrite`` is true; otherwise a ``ValueError`` is raised and the file is
    left untouched.

    Never clobbers a file it can't safely merge into: invalid YAML, or a
    document that isn't the expected ``{suites: [...]}`` shape, raises
    ``ValueError``. A file that parses to ``None`` (empty, or a comments-only
    scaffold) is treated as fresh.

    Returns the list of suite names present in the file after the write.
    """
    path = Path(path)
    name = suite.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("suite must have a non-empty string 'name'")

    data = None
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ValueError(
                f"{path} exists but is not valid YAML ({e}). "
                f"Fix the file, or write the new suite {elsewhere_hint}."
            ) from e
        if data is not None and not (
            isinstance(data, dict) and isinstance(data.get("suites"), list)
        ):
            raise ValueError(
                f"{path} exists but doesn't have the expected top-level "
                f"'suites:' list -- refusing to overwrite it. "
                f"Fix the file, or write the new suite {elsewhere_hint}."
            )
    if data is None:
        data = {"suites": []}

    suites = data["suites"]
    existing_idx = next(
        (i for i, s in enumerate(suites)
         if isinstance(s, dict) and s.get("name") == name),
        None,
    )
    if existing_idx is not None:
        if not overwrite:
            raise ValueError(
                f"a suite named {name!r} already exists in {path}; "
                f"pass overwrite=True to replace it"
            )
        suites[existing_idx] = suite
    else:
        suites.append(suite)

    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return [s["name"] for s in suites if isinstance(s, dict) and s.get("name")]


def read_yaml_suite(path, name: str) -> Optional[dict]:
    """Read a suite back out of ``evals.yaml`` into the editor/POST shape.

    The inverse of :func:`build_suite_dict`: returns
    ``{name, model?, prompt?, pipeline?, description?, cases:[{input, context?,
    expect:[{type, value}]}]}`` or ``None`` if the file or the named suite is
    absent. A case's ``grounded`` assertion whose value carries a ``source`` is
    surfaced back as the case's ``context`` field (mirroring how the retrieved
    context was encoded on write), so an edit round-trips cleanly.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not (isinstance(data, dict) and isinstance(data.get("suites"), list)):
        return None
    suite = next((s for s in data["suites"]
                  if isinstance(s, dict) and s.get("name") == name), None)
    if suite is None:
        return None

    cases: list[dict] = []
    for case in suite.get("cases") or []:
        context = None
        expect: list[dict] = []
        for entry in case.get("expect") or []:
            if not isinstance(entry, dict) or not entry:
                continue
            atype, value = next(iter(entry.items()))
            if atype == "grounded" and isinstance(value, dict) and "source" in value:
                context = value.get("source")
                continue
            expect.append({"type": atype, "value": value})
        cases.append({"input": case.get("input"), "context": context, "expect": expect})

    out: dict = {"name": suite.get("name"), "cases": cases}
    for key in ("model", "prompt", "pipeline", "description"):
        if suite.get(key):
            out[key] = suite[key]
    return out


def latest_recorded_context(storage, prompt_name: str) -> Optional[str]:
    """The most recent retrieved context captured for ``prompt_name`` via
    ``track_context`` (stored as the ``"<prompt_name>:context"`` prompt).
    Returns the joined chunk text, or ``None`` if nothing was captured."""
    if not prompt_name:
        return None
    try:
        rec = storage.get_prompt(f"{prompt_name}:context")
    except Exception:
        return None
    return getattr(rec, "content", None) if rec is not None else None


def build_suite_dict(
    name: str,
    cases: list[dict],
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    pipeline: Optional[str] = None,
    description: str = "",
) -> dict:
    """Assemble a suite mapping in the schema ``load_yaml_suites`` accepts.

    ``cases`` is a list of ``{input, context?, expect?}`` dicts where each
    ``expect`` entry is ``{"type": <assertion>, "value": <value>}``. A case's
    retrieved ``context`` (RAG) is made load-bearing by emitting a ``grounded``
    assertion against it, unless the case already carries one.
    """
    suite: dict = {"name": name}
    if description:
        suite["description"] = description
    if pipeline:
        suite["pipeline"] = pipeline
    else:
        suite["model"] = model
        suite["prompt"] = prompt

    yaml_cases: list[dict] = []
    for case in cases:
        expect: list[dict] = []
        has_grounded = False
        for item in case.get("expect") or []:
            atype = item.get("type")
            if not atype:
                continue
            if atype == "grounded":
                has_grounded = True
            expect.append({atype: item.get("value")})
        context = case.get("context")
        if context and not has_grounded:
            # The retrieved context is the source the answer should be grounded
            # in -- express it as the documented `grounded` assertion so it is
            # actually exercised at run time (rather than an inert field).
            expect.append({"grounded": {"source": context, "threshold": 0.8}})
        yaml_cases.append({"input": case.get("input"), "expect": expect})

    suite["cases"] = yaml_cases
    return suite


# ---------------------------------------------------------------------------
# Sourcing candidate cases from stored golden examples / positive feedback.
# ---------------------------------------------------------------------------

def _golden_candidates(storage, name: Optional[str], limit: int) -> list[dict]:
    if not storage.supports("list_golden_examples"):
        return []
    if name:
        prompt_names = [name]
    else:
        prompt_names = [p["name"] for p in storage.list_prompt_summaries(limit=500)]

    out: list[dict] = []
    for pname in prompt_names:
        for ex in storage.list_golden_examples(pname):
            out.append({
                "question": ex.get("input_text"),
                "context": None,
                "response": ex.get("reference_output"),
                "source": "golden",
                "request_id": None,
                "prompt_name": ex.get("prompt_name") or pname,
            })
            if len(out) >= limit:
                return out
    return out


def _context_for(storage, prompt_name: Optional[str], request_id: Optional[str]) -> Optional[str]:
    """Retrieved context correlated to an invocation by ``request_id``.

    ``track_context`` persists the retrieved chunks as a prompt named
    ``"<prompt_name>:context"``; the correlating ``request_id`` (if any) lives
    in that record's metadata. Returns the joined chunk text, or ``None``.
    """
    if not prompt_name or not request_id:
        return None
    try:
        records = storage.list_prompts(name=f"{prompt_name}:context", limit=200)
    except Exception:
        return None
    for rec in records:
        meta = getattr(rec, "metadata", None) or {}
        if meta.get("request_id") == request_id:
            return rec.content
    return None


def _feedback_candidates(storage, name: Optional[str], min_rating: float, limit: int) -> list[dict]:
    if not storage.supports("list_feedback"):
        return []
    # NOTE: list_feedback's own `min_rating` keeps ratings <= that (for surfacing
    # NEGATIVE feedback), which is the opposite of what we want. Fetch broadly
    # and filter for rating >= min_rating here.
    rows = storage.list_feedback(
        name=name, days=36500, limit=max(limit * 4, 200), offset=0,
    )
    out: list[dict] = []
    for r in rows:
        rating = r.get("rating")
        if rating is None or rating < min_rating:
            continue
        prompt_name = r.get("prompt_name")
        request_id = r.get("request_id")
        question = response = None
        inv_id = r.get("invocation_id")
        if inv_id and storage.supports("get_invocation"):
            inv = storage.get_invocation(inv_id)
            if inv:
                question = inv.get("input_text")
                response = inv.get("output_text")
                request_id = inv.get("request_id") or request_id
                prompt_name = inv.get("prompt_name") or prompt_name
        out.append({
            "question": question,
            "context": _context_for(storage, prompt_name, request_id),
            "response": response,
            "source": "feedback",
            "request_id": request_id,
            "prompt_name": prompt_name,
        })
        if len(out) >= limit:
            break
    return out


def suite_candidates(
    storage,
    source: str,
    name: Optional[str] = None,
    min_rating: float = 1.0,
    limit: int = 50,
) -> list[dict]:
    """Candidate eval cases sourced from stored data.

    ``source``:

    * ``"golden"`` -- from golden examples (optionally scoped to prompt ``name``).
    * ``"feedback"`` -- from invocations whose end-user feedback rating is
      ``>= min_rating``, pulling captured input/output when present and the
      retrieved context when it correlates by ``request_id``.

    Each candidate is a dict::

        {question, context (str|None), response, source,
         request_id (str|None), prompt_name}

    ``question``/``response``/``context`` may be ``None`` when the invocation
    was recorded without ``track_invocation(capture=True)`` / ``track_context``.
    """
    if source == "golden":
        return _golden_candidates(storage, name, limit)
    if source == "feedback":
        return _feedback_candidates(storage, name, min_rating, limit)
    raise ValueError(f"unknown source {source!r}; expected 'golden' or 'feedback'")


CAPTURE_NOTE = (
    "Question/response/context are only available for invocations recorded with "
    "track_invocation(capture=True) and, for context, track_context(). Where "
    "capture was off, those fields are empty -- fill them in manually before saving."
)
