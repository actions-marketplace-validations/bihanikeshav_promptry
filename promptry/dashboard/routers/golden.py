"""Golden-example (eval-from-trace) routes: per-prompt promoted traces."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class _AddExampleReq(BaseModel):
    invocation_id: int


@router.get("/api/prompts/{name}/examples")
def list_examples(name: str):
    """Golden examples (promoted traces) for a prompt."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    if not storage.supports("list_golden_examples"):
        return {"examples": []}
    return {"examples": storage.list_golden_examples(name)}


@router.post("/api/prompts/{name}/examples")
def add_example(name: str, body: _AddExampleReq):
    """Promote a captured invocation into a golden example for this prompt:
    its recorded output becomes the reference a re-run is scored against."""
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    rec = storage.get_invocation(body.invocation_id) if storage.supports("get_invocation") else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Invocation not found")
    if not rec.get("input_text"):
        raise HTTPException(status_code=400, detail="Invocation has no captured input to replay")
    meta = rec.get("metadata") or {}
    ex_id = storage.add_golden_example(
        prompt_name=name,
        input_text=rec["input_text"],
        reference_output=rec.get("output_text"),
        source_invocation_id=body.invocation_id,
        model=meta.get("model"),
    )
    return {"ok": True, "id": ex_id}


@router.delete("/api/examples/{example_id}")
def delete_example(example_id: int):
    from promptry.dashboard.server import get_storage
    storage = get_storage()
    ok = storage.delete_golden_example(example_id) if storage.supports("delete_golden_example") else False
    return {"ok": ok}


class _RunExamplesReq(BaseModel):
    model: str
    threshold: float = 0.8


@router.post("/api/prompts/{name}/examples/run")
def run_examples(name: str, body: _RunExamplesReq):
    """Re-issue every golden example through a model and score each output's
    similarity to its recorded reference; returns per-example results + accuracy."""
    from promptry.eval_from_trace import run_golden_set

    from promptry.dashboard.server import get_storage
    try:
        return run_golden_set(get_storage(), name, body.model, threshold=body.threshold)
    except ImportError:
        raise HTTPException(status_code=503, detail="Eval endpoint is unavailable: litellm dependency missing. Reinstall with pip install --upgrade promptry")
