"""Budget enforcement — turn tracked budgets into a hard spend ceiling.

Budgets (created via the dashboard / API) are informational by default: the
dashboard shows spend vs. limit and a breach flag. Set
``PROMPTRY_ENFORCE_BUDGETS=1`` to make a breached budget actually *block* new
model calls at the ``promptry.llm.completion`` seam, so a runaway loop can't
keep spending past the cap.

Enforcement is opt-in so existing installs are unchanged, and it is fail-open:
any error reading budgets lets the call through (a monitoring feature must
never take down the thing it monitors).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised by enforce_budgets() when a matching budget is over its limit."""


def budgets_enforced() -> bool:
    return (os.environ.get("PROMPTRY_ENFORCE_BUDGETS", "").strip().lower()
            in ("1", "true", "yes", "on"))


def _get_storage():
    try:
        from promptry.storage import get_storage
        return get_storage()
    except Exception:
        return None


def _matches(budget: dict, prompt_name: Optional[str]) -> bool:
    scope = budget.get("scope")
    if scope == "global":
        return True
    if prompt_name is None:
        return False
    mod = prompt_name.split(".")[0] if "." in prompt_name else prompt_name
    target = budget.get("target")
    return (scope == "module" and target == mod) or (scope == "prompt" and target == prompt_name)


def breached_budgets(prompt_name: Optional[str] = None, storage=None) -> list[dict]:
    """Breached budgets that apply to this call (global always applies;
    module/prompt only when prompt_name is known). Fail-open: [] on any error."""
    storage = storage or _get_storage()
    if storage is None or not storage.supports("get_budget_status"):
        return []
    try:
        statuses = storage.get_budget_status()
    except Exception:
        log.debug("budget status read failed; allowing call", exc_info=True)
        return []
    return [b for b in statuses if b.get("breached") and _matches(b, prompt_name)]


def enforce_budgets(prompt_name: Optional[str] = None, storage=None) -> None:
    """Raise BudgetExceededError if enforcement is on and a matching budget is
    over its limit. No-op (and no storage access) when enforcement is off."""
    if not budgets_enforced():
        return
    breached = breached_budgets(prompt_name, storage)
    if not breached:
        return
    b = breached[0]
    raise BudgetExceededError(
        f"{b.get('scope')} budget exceeded: "
        f"${b.get('spend', 0):.4f} spent of ${b.get('limit_usd', 0):.2f} "
        f"limit ({b.get('period')}). Set PROMPTRY_ENFORCE_BUDGETS=0 to disable "
        f"blocking."
    )
