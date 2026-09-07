"""The data/ cache. Append-only, idempotent, disposable.

Nothing here is a source of truth. Deleting data/ and re-running ingest must fully
restore it.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from finlink.ingest.base import Fundamentals, NewsItem, PriceBar


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.prices = root / "data" / "prices"
        self.metrics = root / "data" / "metrics"
        self.news = root / "data" / "news"

    def ensure(self) -> None:
        for d in (self.prices, self.metrics, self.news):
            d.mkdir(parents=True, exist_ok=True)

    # ---- prices -------------------------------------------------------
    def price_path(self, ticker: str) -> Path:
        return self.prices / f"{ticker}.csv"

    def append_prices(self, bars: list[PriceBar]) -> tuple[int, int]:
        """Idempotent: dedupes by (ticker, day). Returns (added, total)."""
        if not bars:
            return 0, 0
        ticker = bars[0].ticker
        path = self.price_path(ticker)
        existing: dict[str, dict[str, str]] = {}
        if path.exists():
            existing = {row["day"]: row for row in self._read_price_rows(path)}

        added = 0
        for b in bars:
            if b.day.isoformat() not in existing:
                added += 1
            existing[b.day.isoformat()] = {
                "day": b.day.isoformat(),
                "open": str(b.open),
                "high": str(b.high),
                "low": str(b.low),
                "close": str(b.close),
                "adj_close": str(b.adj_close),
                "volume": str(b.volume),
                "currency": b.currency,
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [existing[k] for k in sorted(existing)]
        with path.open("w", newline="", encoding="utf-8") as f:
            fields = ["day", "open", "high", "low", "close", "adj_close", "volume", "currency"]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        return added, len(rows)

    def _read_price_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def load_prices(self, ticker: str) -> list[PriceBar]:
        path = self.price_path(ticker)
        if not path.exists():
            return []
        out = []
        for row in self._read_price_rows(path):
            out.append(
                PriceBar(
                    ticker=ticker,
                    day=date.fromisoformat(row["day"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    adj_close=Decimal(row["adj_close"]),
                    volume=int(row["volume"] or 0),
                    currency=row["currency"],
                )
            )
        return out

    def latest_price(self, ticker: str) -> Decimal | None:
        bars = self.load_prices(ticker)
        return bars[-1].close if bars else None

    # ---- fundamentals -------------------------------------------------
    def write_fundamentals(self, f: Fundamentals) -> None:
        self.metrics.mkdir(parents=True, exist_ok=True)
        path = self.metrics / f"{f.ticker}.json"
        path.write_text(
            json.dumps(
                {
                    "ticker": f.ticker,
                    "currency": f.currency,
                    "as_of": f.as_of,
                    "metrics": {k: str(v) for k, v in f.metrics.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_fundamentals(self, ticker: str) -> Fundamentals | None:
        path = self.metrics / f"{ticker}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Fundamentals(
            ticker=raw["ticker"],
            currency=raw["currency"],
            as_of=raw.get("as_of", ""),
            metrics={k: Decimal(v) for k, v in (raw.get("metrics") or {}).items()},
        )

    # ---- news ---------------------------------------------------------
    def news_path(self, ticker: str) -> Path:
        return self.news / f"{ticker}.jsonl"

    def append_news(self, items: list[NewsItem]) -> int:
        """Dedupes by URL. Items without a URL are DISCARDED (provenance rule)."""
        path = self.news_path(items[0].ticker) if items else None
        if path is None:
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        if path.exists():
            seen = {
                json.loads(ln)["url"]
                for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            }
        added = 0
        with path.open("a", encoding="utf-8") as f:
            for it in items:
                if not it.url or not it.url.startswith(("http://", "https://")):
                    continue  # provenance or nothing
                if it.url in seen:
                    continue
                f.write(json.dumps(it.__dict__, ensure_ascii=False) + "\n")
                seen.add(it.url)
                added += 1
        return added

    def load_news(self, ticker: str) -> list[NewsItem]:
        path = self.news_path(ticker)
        if not path.exists():
            return []
        out = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(NewsItem(**json.loads(ln)))
        return out
