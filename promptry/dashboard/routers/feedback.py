"""End-user feedback and prompt-vote routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from promptry.dashboard import auth as authlib

router = APIRouter()


class _FeedbackIn(BaseModel):
    request_id: str
    rating: Optional[float] = None
    comment: Optional[str] = None
    source: Optional[str] = None


@router.post("/api/feedback")
def submit_feedback(body: _FeedbackIn, request: Request,
                    _actor: authlib.Actor = Depends(authlib.require_role("viewer"))):
    """Ingest an end-user rating/comment from the host app, correlated to an
    invocation by request_id (the id the app passed to track_invocation).

    Only viewer role required: this is a data-capture endpoint host apps call
    (typically with the shared token) whenever an end user rates a response."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("save_feedback"):
        raise HTTPException(status_code=501, detail="feedback not supported by this backend")
    fid = storage.save_feedback(body.request_id, rating=body.rating, comment=body.comment, source=body.source)
    authlib.audit_event(request, "feedback.create", target=body.request_id,
                        detail={"rating": body.rating})
    return {"ok": True, "id": fid}


@router.get("/api/feedback")
def list_feedback(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    only_comments: bool = Query(default=False),
    min_rating: Optional[float] = Query(default=None),
    q: Optional[str] = Query(default=None),
):
    """End-user feedback rows, newest first, paged via limit/offset. q searches
    comment + prompt name. Each row links back to its invocation."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("list_feedback"):
        return {"feedback": []}
    return {"feedback": storage.list_feedback(
        name=name, days=days, limit=limit, offset=offset,
        only_comments=only_comments, min_rating=min_rating, q=q,
    )}


@router.get("/api/feedback/stats")
def feedback_stats(days: int = Query(default=30, ge=1)):
    """Satisfaction + counts + per-prompt breakdown + daily positive-rate spark."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("get_feedback_stats"):
        return {"days": days, "total": 0, "rated": 0, "positive_rate": None, "by_prompt": [], "sparkline": []}
    return storage.get_feedback_stats(days=days)


# ---- Votes ----

@router.get("/api/votes/stats")
def vote_stats(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    return storage.get_vote_stats(prompt_name=name, days=days)


@router.get("/api/votes")
def list_votes(
    name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
    offset: int = Query(default=0),
    limit: int = Query(default=50),
):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    return storage.get_votes(prompt_name=name, days=days, offset=offset, limit=limit)


@router.get("/api/votes/analyze")
def vote_analyze(
    name: str = Query(...),
    days: int = Query(default=30, ge=1),
    # Runs a (paid) judge model — gate behind editor like the other
    # cost-incurring routes so a viewer can't rack up judge spend.
    _actor: authlib.Actor = Depends(authlib.require_role("editor")),
):
    from promptry.feedback import analyze_votes
    from promptry.assertions import get_judge

    from promptry.dashboard.server import get_storage
    storage = get_storage()
    judge = get_judge()
    return analyze_votes(name, days=days, judge=judge, storage=storage)
