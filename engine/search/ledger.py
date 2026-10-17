"""Cross-run provider ledger: monthly usage per provider, used to stay inside free allowances.

The ledger is deliberately not linked to runs by foreign key, so deleting research data never
erases the record of what was spent. It holds no personal data (provider, operation, units, time).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..config import Providers
from ..store.repo import iso

CAP_FIELD = {"brave": ("brave_monthly_cap_requests", "request"), "tavily": ("tavily_monthly_cap_credits", "credit"),
             "exa": ("exa_monthly_cap_usd", "usd")}


def month_start(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def used_this_month(conn: sqlite3.Connection, provider: str, now: datetime | None = None) -> float:
    row = conn.execute("SELECT COALESCE(SUM(units), 0) FROM provider_ledger WHERE provider=? AND at >= ?",
                       (provider, month_start(now))).fetchone()
    return float(row[0])


def cap_for(provider: str, settings: Providers) -> tuple[float, str]:
    field, unit = CAP_FIELD[provider]
    return float(getattr(settings, field)), unit


def would_exceed(conn: sqlite3.Connection, provider: str, expected_units: float, settings: Providers) -> str | None:
    cap, unit = cap_for(provider, settings)
    used = used_this_month(conn, provider)
    if used + expected_units > cap:
        return f"{provider}: {used:g} {unit}s used this month + {expected_units:g} > cap {cap:g}"
    return None


def record(conn: sqlite3.Connection, provider: str, operation: str, unit: str, units: float, estimated_usd: float,
           reported_usd: float | None, run_id: str | None) -> None:
    conn.execute("INSERT INTO provider_ledger (provider, operation, unit, units, estimated_cost_usd, reported_cost_usd,"
                 " run_id, at) VALUES (?,?,?,?,?,?,?,?)",
                 (provider, operation, unit, units, estimated_usd, reported_usd, run_id, iso()))


CAVEAT = ("Local engine record of this engine's own requests (per-response usage fields and list-price "
          "estimates). NOT a verified account balance: Tavily's account counter (GET /usage) did not reflect this "
          "engine's usage when checked on 2026-10-17 and remains unreconciled; usage made outside this engine is "
          "invisible here.")


def summary(conn: sqlite3.Connection, settings: Providers) -> dict:
    out: dict = {"_caveat": CAVEAT}
    for provider in CAP_FIELD:
        cap, unit = cap_for(provider, settings)
        row = conn.execute("""SELECT COUNT(*), COALESCE(SUM(units),0), COALESCE(SUM(estimated_cost_usd),0),
                                     SUM(reported_cost_usd) FROM provider_ledger WHERE provider=? AND at >= ?""",
                           (provider, month_start())).fetchone()
        out[provider] = {"operations": row[0], "units_used": round(row[1], 4), "unit": unit, "monthly_cap": cap,
                         "remaining_under_cap": round(cap - row[1], 4), "estimated_list_usd": round(row[2], 4),
                         "provider_reported_usd": None if row[3] is None else round(row[3], 6)}
    return out
