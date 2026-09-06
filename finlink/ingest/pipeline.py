"""Pipeline P2 — ingestion. Idempotent, provenance-first, cache-only."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from finlink.ingest.base import MarketDataDriver, NewsDriver
from finlink.ingest.store import Store


@dataclass
class IngestResult:
    ticker: str
    prices_added: int = 0
    prices_total: int = 0
    news_added: int = 0
    news_total: int = 0
    fundamentals: bool = False
    currency: str = ""
    error: str | None = None
    duration_ms: int = 0


def log_run(root: Path, results: list[IngestResult]) -> None:
    path = root / "logs" / "ingest_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "results": [r.__dict__ for r in results],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ingest_ticker(
    ticker: str,
    store: Store,
    market: MarketDataDriver,
    news: NewsDriver | None = None,
    days: int = 400,
) -> IngestResult:
    started = time.monotonic()
    result = IngestResult(ticker=ticker)
    try:
        bars = market.fetch_prices(ticker, days=days)
        added, total = store.append_prices(bars)
        result.prices_added, result.prices_total = added, total
        result.currency = bars[-1].currency if bars else ""

        fund = market.fetch_fundamentals(ticker)
        store.write_fundamentals(fund)
        result.fundamentals = True
        if not result.currency:
            result.currency = fund.currency

        if news is not None:
            items = news.fetch_news(ticker)
            result.news_added = store.append_news(items)
            result.news_total = len(store.load_news(ticker))
    except Exception as e:  # noqa: BLE001 - surface any provider failure verbatim
        result.error = str(e)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def latest_prices(store: Store, tickers: list[str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for t in tickers:
        p = store.latest_price(t)
        if p is not None:
            out[t] = p
    return out
