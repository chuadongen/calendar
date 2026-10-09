"""Connector for your own money app (not built yet).

Set MONEY_API_URL and the dashboard calls:

    GET {MONEY_API_URL}/weekly?start=YYYY-MM-DD&weeks=8

expecting JSON like:

    {
      "currency": "SGD",
      "weekly_budget": 200,
      "weeks": [
        {"week_start": "2026-10-05", "spent": 152.30,
         "categories": {"Food": 80.50, "Transport": 21.00, "Social": 50.80}}
      ]
    }

`weeks` holds one entry per Monday, oldest first. Missing weeks count as no data.
"""

import os
from datetime import date

import httpx


class MoneyError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("MONEY_API_URL"))


def weekly(start: date, weeks: int, transport=None) -> dict:
    url = os.getenv("MONEY_API_URL", "").rstrip("/")
    with httpx.Client(timeout=10, transport=transport) as c:
        resp = c.get(f"{url}/weekly", params={"start": start.isoformat(), "weeks": weeks})
    if resp.status_code >= 400:
        raise MoneyError(f"Money app returned {resp.status_code}")
    return resp.json()
