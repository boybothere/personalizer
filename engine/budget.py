"""Hard per-run limits, checked before every costed action. Exceeding one raises BudgetExceeded."""

from __future__ import annotations

import time
from collections.abc import Callable

from .config import Budget as BudgetLimits
from .failures import Code

_KINDS = {
    "leads": ("max_leads", Code.BUDGET_LEADS),
    "searches": ("max_search_requests", Code.BUDGET_SEARCHES),
    "fetches": ("max_fetch_attempts", Code.BUDGET_FETCHES),
    "robots": ("max_robots_requests", Code.BUDGET_ROBOTS),
    "bytes": ("max_total_bytes", Code.BUDGET_BYTES),
    "cost": ("max_cost_usd", Code.BUDGET_COST),
}


class BudgetExceeded(Exception):
    def __init__(self, code: Code, detail: str):
        super().__init__(detail)
        self.code = code


class RunBudget:
    def __init__(self, limits: BudgetLimits, clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.spent: dict[str, float] = {k: 0 for k in _KINDS}
        self.cancelled = False

    def elapsed(self) -> float:
        return self.clock() - self.started

    def remaining(self, kind: str) -> float:
        field, _ = _KINDS[kind]
        return getattr(self.limits, field) - self.spent[kind]

    def remaining_seconds(self) -> float:
        return self.limits.max_wall_seconds - self.elapsed()

    def check(self) -> None:
        """Run-wide stop conditions (time, cancellation)."""
        if self.cancelled:
            raise BudgetExceeded(Code.CANCELLED, "run cancelled")
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded(Code.BUDGET_TIME, f"{self.limits.max_wall_seconds}s wall-clock limit reached")

    def charge(self, kind: str, amount: float = 1) -> None:
        """Reserve `amount` of `kind` or raise without charging."""
        self.check()
        field, code = _KINDS[kind]
        limit = getattr(self.limits, field)
        if self.spent[kind] + amount > limit:
            raise BudgetExceeded(code, f"{kind}: {self.spent[kind]:g} of {limit:g} used, {amount:g} more requested")
        self.spent[kind] += amount

    def record(self, kind: str, amount: float) -> None:
        """Account for usage measured after the fact (bytes actually downloaded)."""
        self.spent[kind] += amount

    def snapshot(self) -> dict:
        return {"spent": dict(self.spent), "elapsed_s": round(self.elapsed(), 3), "limits": self.limits.model_dump()}
