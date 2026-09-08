"""Tencent market-data driver — free, keyless daily OHLCV for HK and US tickers.

Fetches historical daily OHLCV via Tencent's fqkline endpoint
(web.ifzq.gtimg.cn/appstock/app/fqkline/get) used by their stock app. No API
key, no quota, no JS wall.

Symbol mapping (verified live 2026-09-08):
  US:  BABA -> usBABA.N    (Nasdaq-qualified; without .N only latest bar)
       GLD  -> usGLD.AM
       SGOV -> usSGOV.N
  HK:  00700.HK -> hk00700  (4-digit numeric core)
  Other / unknown suffixes raise IngestError (coverage gap, never silent).

Tencent returns up to ~660 daily bars and covers from 2024; ingest default of
400 days fits comfortably.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from finlink.ingest.base import Fundamentals, IngestError, PriceBar

KLINE_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
QUOTE_BASE = "https://qt.gtimg.cn/q="

# US symbols need an exchange suffix or Tencent returns only the latest bar.
US_MIC_SUFFIX = {
    "BABA": "N",   # Nasdaq / NYSE common
    "GLD": "AM",   # NYSE Arca for the GLD ETF
    "SGOV": "N",
}

# Conservative politeness: two kline requests per second max.
MIN_INTERVAL_S = 0.5


class TencentDriver:
    name = "tencent"

    def __init__(self, timeout: int = 20, delay: float = MIN_INTERVAL_S) -> None:
        self._timeout = timeout
        self._delay = delay
        self._last_request = 0.0

    # ---- public driver interface -------------------------------------
    def fetch_prices(self, ticker: str, days: int = 400) -> list[PriceBar]:
        symbol = self._symbol(ticker)
        ccy = self._currency(ticker)
        end = date.today()
        start = end - timedelta(days=days + 180)  # buffer for non-trading days
        rows = self._kline(symbol, start.isoformat(), end.isoformat(), max(days * 2, 400))
        if not rows:
            raise IngestError(f"{ticker}: Tencent returned no price rows for {symbol}")
        bars = []
        for row in rows:
            try:
                day = date.fromisoformat(row[0])
                o = Decimal(str(row[1]))
                close = Decimal(str(row[2]))
                high = Decimal(str(row[3]))
                low = Decimal(str(row[4]))
                vol = int(float(row[5] or 0))
            except (IndexError, ValueError, InvalidOperation) as e:
                raise IngestError(f"{ticker}: malformed Tencent row {row!r}: {e}") from e
            bars.append(
                PriceBar(
                    ticker=ticker, day=day, open=o, high=high, low=low,
                    close=close, adj_close=close,
                    volume=vol,
                    currency=ccy,
                )
            )
        return bars[-min(days, len(bars)) :]

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        """Tencent exposes prices only; return empty metrics.

        Fundamentals (P/E, market cap, margins) are a nice-to-have surfaced by
        `finlink show`; run `finlink ingest --driver alphavantage` for those.
        """
        return Fundamentals(
            ticker=ticker,
            currency=self._currency(ticker),
            metrics={},
            as_of=date.today().isoformat(),
        )

    # ---- tencent internals -------------------------------------------
    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._delay - (now - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _request_json(self, url: str) -> Any:
        self._throttle()
        req = urllib.request.Request(url, headers={"User-Agent": "finlink/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise IngestError(
                f"tencent: HTTP {e.code}: "
                f"{e.read().decode('utf-8', 'replace')[:160]}"
            ) from e
        except urllib.error.URLError as e:
            raise IngestError(f"tencent: network error: {e.reason}") from e

    def _kline(self, symbol: str, start: str, end: str, count: int) -> list[list[str]]:
        url = f"{KLINE_BASE}?param={symbol},day,{start},{end},{count},ALL"
        data = self._request_json(url)
        if not isinstance(data, dict):
            raise IngestError(f"tencent: unexpected response for {symbol}")
        node = data.get("data", {}).get(symbol, {})
        if not isinstance(node, dict):
            raise IngestError(f"tencent: no data node for {symbol}")
        rows = node.get("day")
        if not isinstance(rows, list):
            raise IngestError(f"tencent: no day series for {symbol}: {data.get('msg')!r}")
        return rows

    # ---- symbol / currency helpers -----------------------------------
    def _symbol(self, ticker: str) -> str:
        t = ticker.strip().upper()
        if t.endswith(".HK"):
            # Tencent keeps the HKEX 5-digit code with leading zeros
            # (hk00700, hk00981, ...). Do not strip them.
            core = t[:-3].strip()
            if not core.isdigit() or not (4 <= len(core) <= 6):
                raise IngestError(f"tencent: malformed HK ticker {ticker!r}")
            return f"hk{core.zfill(5)}"
        if t in US_MIC_SUFFIX:
            return f"us{t}.{US_MIC_SUFFIX[t]}"
        if t.isascii() and t.isalnum() and not t.endswith(".ST"):
            # Bare US ticker (e.g. AAPL). Tencent needs a MIC suffix for
            # historical depth (without it only the latest bar comes back);
            # .N is the common Nasdaq/NYSE qualifier.
            return f"us{t}.N"
        raise IngestError(
            f"tencent: unsupported ticker {ticker!r} (supported: US, .HK; "
            f"no coverage for other exchanges)"
        )

    def _currency(self, ticker: str) -> str:
        if ticker.strip().upper().endswith(".HK"):
            return "HKD"
        return "USD"


# Backwards-compatible alias so older tests / tooling can import the driver by
# its old temporary module name.
TencentMarketDriver = TencentDriver

__all__ = ["TencentDriver", "TencentMarketDriver"]
