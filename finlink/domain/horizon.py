"""Thesis horizon arithmetic. Pure date maths, no I/O.

A hypothesis whose horizon has passed is `expired`, not `supported`: an untested
claim that simply ran out of time must never look confirmed.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

HORIZON_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([dwmy])\s*$", re.IGNORECASE)


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])  # clamp Jan 31 + 1m
    return date(year, month, day)


def horizon_end(created: date, horizon: str) -> date | None:
    """Resolve '2y' / '6m' / '30d' / '3w' to an end date. None when unusable.

    An unparseable horizon returns None rather than a guess: the caller then leaves
    the hypothesis alone instead of expiring it on a bad parse.
    """
    m = HORIZON_RE.match(horizon or "")
    if not m:
        return None
    amount = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "d":
        return created + timedelta(days=amount)
    if unit == "w":
        return created + timedelta(weeks=amount)
    if unit == "m":
        return _add_months(created, int(round(amount)))
    return _add_months(created, int(round(amount * 12)))


def is_expired(created: date, horizon: str, today: date) -> bool:
    end = horizon_end(created, horizon)
    return end is not None and today > end
