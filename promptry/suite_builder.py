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

from pathlib import Path
from typing import Optional

import yaml


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
