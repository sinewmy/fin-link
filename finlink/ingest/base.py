"""Ingestion data model and driver interface.

Everything ingested is a CACHE (data/, gitignored) except nothing — the source of
truth stays in portfolio/ and theses/. Deleting data/ must always be recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int
    currency: str


@dataclass(frozen=True)
class NewsItem:
    """An item with no resolvable source URL is DISCARDED, never stored."""

    ticker: str
    title: str
    url: str
    published_at: str
    source: str
    summary: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    currency: str
    metrics: dict[str, Decimal] = field(default_factory=dict)
    as_of: str = ""


class IngestError(RuntimeError):
    """Raised when a source cannot serve a ticker. Never store nulls silently."""


@runtime_checkable
class MarketDataDriver(Protocol):
    name: str

    def fetch_prices(self, ticker: str, days: int = 400) -> list[PriceBar]: ...

    def fetch_fundamentals(self, ticker: str) -> Fundamentals: ...


@runtime_checkable
class NewsDriver(Protocol):
    name: str

    def fetch_news(self, ticker: str, limit: int = 30) -> list[NewsItem]: ...
