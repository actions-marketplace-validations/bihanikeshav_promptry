"""Prompt registry, versioning, and per-prompt telemetry routes."""
from __future__ import annotations

import difflib
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from promptry.registry import PromptRegistry

router = APIRouter()


# ---- Prompts ----

@router.get("/api/prompts")
def list_prompts(offset: int = Query(default=0), limit: int = Query(default=200)):
    # Aggregate in SQL so the page limit applies to NAMES, not versions.
    # (A prompt with 86 versions used to fill the whole page and hide the
    # rest.) Fall back to the old row-grouping path for storage backends
    # that don't implement the summaries query yet.
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    summaries = None
    if storage.supports("list_prompt_summaries"):
        try:
            summaries = storage.list_prompt_summaries(offset=offset, limit=limit)
        except Exception:
            summaries = None
    if summaries is not None:
        return summaries

    all_prompts = storage.list_prompts(offset=offset, limit=limit)
    if not all_prompts:
        return []
    by_name: dict[str, list] = {}
    for p in all_prompts:
        by_name.setdefault(p.name, []).append(p)
    result = []
    for name, versions in by_name.items():
        latest = max(versions, key=lambda p: p.version)
        all_tags = set()
        for v in versions:
            all_tags.update(v.tags)
        result.append({
            "name": name,
            "latest_version": latest.version,
            "tags": sorted(all_tags),
        })
    return result


# NOTE: these two static paths MUST be declared before /api/prompts/{name},
# or FastAPI matches "search"/"near-duplicates" as a {name} and shadows them.
@router.get("/api/prompts/search")
def prompts_search(q: str = Query(...), top_k: int = Query(default=10, ge=1, le=50)):
    """Rank prompts by relevance to a free-text query (semantic if embeddings
    are installed, else keyword overlap)."""
    from promptry.dashboard.server import get_storage
    from promptry.prompt_search import search_prompts

    return search_prompts(get_storage(), q, top_k=top_k)


@router.get("/api/prompts/near-duplicates")
def prompts_near_duplicates(threshold: float = Query(default=0.85, ge=0.0, le=1.0)):
    """Pairs of prompts whose latest content is near-identical — likely forks
    that should have been versions, or candidates to merge."""
    from promptry.dashboard.server import get_storage
    from promptry.prompt_search import near_duplicates

    return near_duplicates(get_storage(), threshold=threshold)


@router.get("/api/prompts/diff2")
def prompts_diff2(a: str = Query(...), b: str = Query(...)):
    """Cross-prompt diff + prompt-prefix cache analysis between the latest
    content of two (possibly unrelated) prompt names. Backs near-duplicate
    consolidation: given a pair flagged by /api/prompts/near-duplicates,
    show exactly what differs and whether reordering static text to the
    front would improve prefix-cache hit rate."""
    from promptry.dashboard.server import get_storage
    from promptry.prompt_diff import cache_analysis, diff_prompts

    storage = get_storage()
    rec_a = storage.get_prompt(a)
    rec_b = storage.get_prompt(b)
    if rec_a is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{a}' not found")
    if rec_b is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{b}' not found")

    diff = diff_prompts(rec_a.content, rec_b.content)
    analysis = cache_analysis(rec_a.content, rec_b.content)

    return {
        "a": {"name": a, "latest_version": rec_a.version, "content": rec_a.content},
        "b": {"name": b, "latest_version": rec_b.version, "content": rec_b.content},
        "diff": diff,
        "shared_prefix_chars": analysis["shared_prefix_chars"],
        "shared_prefix_ratio": analysis["shared_prefix_ratio"],
        "cache_suggestion": {
            "suggested": analysis["suggested"],
            "rationale": analysis["rationale"],
        },
    }


@router.get("/api/prompts/{name}")
def prompt_versions(name: str):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    versions = storage.list_prompts(name=name)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return {
        "versions": [
            {
                "version": v.version,
                "hash": v.hash,
                "created_at": v.created_at,
                "tags": v.tags,
            }
            for v in versions
        ]
    }


@router.get("/api/prompts/{name}/content")
def prompt_content(name: str, v: Optional[int] = Query(default=None)):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    record = storage.get_prompt(name, version=v)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    from promptry.dashboard.util import dc_to_dict as _dc_to_dict
    out = _dc_to_dict(record)
    # Attach template variables + lint findings so the UI can show the
    # template's contract and warn about footguns.
    try:
        from promptry.lint import extract_variables, lint_prompt
        out["variables"] = extract_variables(record.content)
        out["lint"] = lint_prompt(record.content)
    except Exception:
        out["variables"] = []
        out["lint"] = []
    return out


@router.post("/api/prompts/lint")
def lint_prompt_endpoint(body: dict):
    """Lint arbitrary prompt text (live, as the user types in the editor)."""
    from promptry.lint import extract_variables, lint_prompt
    content = body.get("content", "")
    return {"variables": extract_variables(content), "lint": lint_prompt(content)}


class _PromptEdit(BaseModel):
    content: str


@router.post("/api/prompts/{name}/content")
def update_prompt_content(name: str, body: _PromptEdit):
    """Save an edited prompt as a new version from the dashboard.

    Dedups by content hash (same as track()), so saving an unchanged
    body is a no-op that returns the existing latest version. Apps that
    fetch their templates from promptry by name will pick up the new
    version on their next cache refresh.
    """
    from promptry.dashboard.server import get_storage
    from promptry.prompts import normalize_template

    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Prompt content cannot be empty")
    # Canonicalize $name/${name} -> {{name}} on save so the registry stores one
    # consistent syntax. (Single-brace {name} is left as authored — it renders
    # fine value-driven, and can't be told from JSON without the variable set.)
    content = normalize_template(content)
    storage = get_storage()
    h = PromptRegistry.content_hash(content)
    # force=True: an explicit edit always becomes the latest version (so a
    # revert to older content sticks), no-op only if identical to current latest.
    record = storage.save_prompt(name=name, content=content, content_hash=h,
                                 metadata={"source": "dashboard_edit"}, force=True)
    return {"ok": True, "name": name, "version": record.version, "hash": record.hash}


@router.get("/api/prompts/{name}/runs")
def prompt_runs(name: str, limit: int = Query(default=50, ge=1)):
    """Eval runs that exercised this prompt (newest first) for the prompt↔eval
    linkage on PromptDetail."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("get_runs_for_prompt"):
        return {"runs": []}
    return {"runs": storage.get_runs_for_prompt(name, limit=limit)}


@router.get("/api/prompts/{name}/stats")
def prompt_stats(name: str, days: int = Query(default=30, ge=1)):
    """Per-call distribution stats for a prompt (input/output tokens, cost,
    latency) over the window, plus an input-size histogram."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    return storage.get_invocation_stats(name, days=days)


@router.get("/api/prompts/{name}/online-drift")
def prompt_online_drift(name: str, days: int = Query(default=30, ge=1)):
    """Distribution shift in a prompt's live telemetry over the window:
    rating, cost, latency, and token counts, recent-half vs older-half."""
    from promptry.dashboard.server import get_storage
    from promptry.drift import online_drift

    storage = get_storage()
    return online_drift(storage, name, days=days)


class _PromoteReq(BaseModel):
    version: int
    env: str = "prod"


@router.post("/api/prompts/{name}/promote")
def promote_prompt(name: str, body: _PromoteReq):
    """Point an environment tag (dev/staging/prod) at a specific version so
    render_prompt(env=...) serves it — a gate between editing and going live."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("set_prompt_env"):
        raise HTTPException(status_code=501, detail="promotion not supported by this backend")
    ok = storage.set_prompt_env(name, body.version, body.env)
    if not ok:
        raise HTTPException(status_code=404, detail=f"{name} v{body.version} not found")
    return {"ok": True, "name": name, "version": body.version, "env": body.env}


@router.post("/api/prompts/{name}/prune")
def prune_prompt(name: str, keep: int = Query(default=1, ge=1)):
    """Collapse a prompt's version history to the newest *keep* versions.

    Use on legacy prompts polluted with baked template+data versions."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("prune_prompt_versions"):
        raise HTTPException(status_code=501, detail="pruning not supported by this storage backend")
    deleted = storage.prune_prompt_versions(name, keep_last=keep)
    return {"ok": True, "name": name, "deleted": deleted, "kept": keep}


@router.get("/api/prompts/{name}/diff")
def prompt_diff(name: str, v1: int = Query(...), v2: int = Query(...)):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    rec1 = storage.get_prompt(name, version=v1)
    rec2 = storage.get_prompt(name, version=v2)
    if rec1 is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' v{v1} not found")
    if rec2 is None:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' v{v2} not found")

    old_lines = rec1.content.splitlines(keepends=True)
    new_lines = rec2.content.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    lines = []
    additions = 0
    deletions = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx, line in enumerate(old_lines[i1:i2]):
                lines.append({
                    "type": "unchanged",
                    "old_num": i1 + idx + 1,
                    "new_num": j1 + idx + 1,
                    "content": line.rstrip("\n\r"),
                })
        elif tag == "delete":
            for idx, line in enumerate(old_lines[i1:i2]):
                lines.append({
                    "type": "deleted",
                    "old_num": i1 + idx + 1,
                    "new_num": None,
                    "content": line.rstrip("\n\r"),
                })
                deletions += 1
        elif tag == "insert":
            for idx, line in enumerate(new_lines[j1:j2]):
                lines.append({
                    "type": "added",
                    "old_num": None,
                    "new_num": j1 + idx + 1,
                    "content": line.rstrip("\n\r"),
                })
                additions += 1
        elif tag == "replace":
            for idx, line in enumerate(old_lines[i1:i2]):
                lines.append({
                    "type": "deleted",
                    "old_num": i1 + idx + 1,
                    "new_num": None,
                    "content": line.rstrip("\n\r"),
                })
                deletions += 1
            for idx, line in enumerate(new_lines[j1:j2]):
                lines.append({
                    "type": "added",
                    "old_num": None,
                    "new_num": j1 + idx + 1,
                    "content": line.rstrip("\n\r"),
                })
                additions += 1

    return {"additions": additions, "deletions": deletions, "lines": lines}
