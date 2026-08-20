"""Playground routes: ad-hoc model calls and lightweight assertion checks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from promptry.dashboard import auth as authlib

router = APIRouter()


class _PlaygroundModelReq(BaseModel):
    model: str
    system: str = ""
    user: str
    context: str = ""
    temperature: float = 0.7


@router.post("/api/playground/model")
def playground_model(body: _PlaygroundModelReq,
                     _actor: authlib.Actor = Depends(authlib.require_role("editor"))):
    """Run a single prompt against a live model and return the output with
    token usage and cost. Used by the Playground's model comparison.

    Calls the provider via litellm (so any model litellm supports works,
    given the right API key in the environment). Cost is computed from
    promptry's rate table.
    """
    import time as _time
    from promptry import llm

    messages = []
    if body.system.strip():
        messages.append({"role": "system", "content": body.system})
    user_content = body.user
    if body.context.strip():
        # Retrieved context goes ahead of the question, clearly delimited.
        user_content = f"Context:\n{body.context}\n\n{body.user}"
    messages.append({"role": "user", "content": user_content})

    start = _time.time()
    try:
        # Raw response so we can read token usage; text is pulled out below.
        resp = llm.completion(
            model=body.model, messages=messages, temperature=body.temperature,
        )
    except ImportError:
        raise HTTPException(status_code=503, detail="Playground is unavailable: litellm dependency missing. Reinstall with pip install --upgrade promptry")
    except Exception as e:
        # Surface provider/auth errors clearly to the dashboard.
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")
    latency_ms = round((_time.time() - start) * 1000)

    text = llm._content(resp)
    usage = getattr(resp, "usage", None)
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

    cost = 0.0
    try:
        from promptry.pricing import calculate_cost
        cost = calculate_cost(body.model, tokens_in=tokens_in, tokens_out=tokens_out) or 0.0
    except Exception:
        pass

    return {
        "response": text,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
    }


@router.post("/api/playground/eval")
async def playground_eval(request: Request,
                          _actor: authlib.Actor = Depends(authlib.require_role("editor"))):
    """Run lightweight assertions against a user-provided mock response.

    Accepts a JSON body with:
      - response (str): the mock LLM output to evaluate
      - assertions (list[dict]): each dict has:
          - type: "contains" | "not_contains" | "json_valid" | "matches"
          - value: argument for the assertion (keywords list, pattern, etc.)
          - options: optional dict of extra args (case_sensitive, fullmatch)

    Returns a list of assertion results with pass/fail and details.
    """
    import re as _re
    import json as _json

    body = await request.json()
    response_text = body.get("response", "")
    assertion_defs = body.get("assertions", [])

    if not response_text:
        raise HTTPException(status_code=400, detail="response field is required")
    if not assertion_defs:
        raise HTTPException(status_code=400, detail="assertions list is required")

    results = []
    for i, adef in enumerate(assertion_defs):
        atype = adef.get("type", "")
        value = adef.get("value")
        options = adef.get("options", {})
        result = {"index": i, "type": atype, "passed": False, "score": 0.0, "details": {}}

        try:
            if atype == "contains":
                keywords = value if isinstance(value, list) else [value]
                case_sensitive = options.get("case_sensitive", False)
                check = response_text if case_sensitive else response_text.lower()
                found = []
                missing = []
                for kw in keywords:
                    if (kw if case_sensitive else kw.lower()) in check:
                        found.append(kw)
                    else:
                        missing.append(kw)
                score = len(found) / len(keywords) if keywords else 1.0
                passed = len(missing) == 0
                result.update(passed=passed, score=score, details={"found": found, "missing": missing})

            elif atype == "not_contains":
                keywords = value if isinstance(value, list) else [value]
                case_sensitive = options.get("case_sensitive", False)
                check = response_text if case_sensitive else response_text.lower()
                found_bad = []
                for kw in keywords:
                    if (kw if case_sensitive else kw.lower()) in check:
                        found_bad.append(kw)
                score = 1.0 - (len(found_bad) / len(keywords)) if keywords else 1.0
                passed = len(found_bad) == 0
                result.update(passed=passed, score=score, details={"found_forbidden": found_bad})

            elif atype == "json_valid":
                try:
                    parsed = _json.loads(response_text.strip())
                    result.update(passed=True, score=1.0, details={"parsed_type": type(parsed).__name__})
                except _json.JSONDecodeError as e:
                    result.update(passed=False, score=0.0, details={"error": str(e)})

            elif atype == "matches":
                pattern = value or ""
                fullmatch = options.get("fullmatch", True)
                # Python's `re` has no backtracking limit; a catastrophic
                # pattern against long input can hang the worker. Bound both so
                # this endpoint can't be turned into a CPU DoS.
                if len(pattern) > 1000 or len(response_text) > 100_000:
                    result.update(
                        passed=False, score=0.0,
                        details={"error": "pattern or input too large to match safely"},
                    )
                else:
                    try:
                        compiled = _re.compile(pattern, _re.DOTALL)
                        text_stripped = response_text.strip()
                        match = compiled.fullmatch(text_stripped) if fullmatch else compiled.search(text_stripped)
                        passed = match is not None
                        details = {"pattern": pattern, "fullmatch": fullmatch}
                        if match:
                            details["matched"] = match.group()[:200]
                        result.update(passed=passed, score=1.0 if passed else 0.0, details=details)
                    except _re.error as e:
                        result.update(passed=False, score=0.0, details={"error": f"Invalid regex: {e}"})

            else:
                result.update(
                    passed=False,
                    score=0.0,
                    details={"error": f"Unknown assertion type: {atype}"},
                )
        except Exception as e:
            result.update(passed=False, score=0.0, details={"error": str(e)})

        results.append(result)

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    overall_score = sum(r["score"] for r in results) / total if total else 0.0

    return {
        "overall_passed": passed_count == total,
        "overall_score": overall_score,
        "passed_count": passed_count,
        "total_count": total,
        "results": results,
    }
