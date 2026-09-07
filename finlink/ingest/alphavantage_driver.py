"""Alpha Vantage market-data driver (free tier, API key required).

Fetches historical daily OHLC via TIME_SERIES_DAILY and currency via OVERVIEW.
Free tier: 5 requests/min, 25 requests/day — `ingest` should run once and cache.

Ticker mapping (Alpha Vantage conventions, verified from their docs/search):
  US:    AAPL, GLD, SGOV, BABA   (no suffix)
  HK:    0700.HK  -> 0700.HK    (Alpha Vantage uses the 4-digit code with .HK, e.g. 0168.HK)
  SE:    INVE-B.ST -> INVE-B.ST (as-is; Stockholm uses .ST)
The driver normalises the local ticker to the Alpha Vantage symbol.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from finlink.ingest.base import Fundamentals, IngestError, PriceBar

BASE = "https://www.alphavantage.co/query"


class AlphaVantageDriver:
    name = "alphavantage"

    # Free tier: 5 requests per minute, 25 per day. Keep ~13s between calls to
    # stay under 5/min, and a daily cap so a misconfigured run can't burn the
    # whole day's quota.
    MIN_INTERVAL_S = 13.0
    MAX_DAILY = 24

    def __init__(self, api_key: str | list[str], timeout: int = 20) -> None:
        keys = [k for k in ([api_key] if isinstance(api_key, str) else list(api_key)) if k]
        if not keys:
            raise IngestError(
                "Alpha Vantage driver needs an API key. "
                "Set ALPHAVANTAGE_API_KEY (or ALPHAVANTAGE_API_KEY_2) in .env "
                "or export it. Multiple keys are used round-robin, and a key that "
                "hits the daily cap is skipped for the rest of the run."
            )
        self._api_keys = keys
        self._timeout = timeout
        self._lock = threading.Lock()
        # per-key request state: iso-day -> {"calls": int, "last": float}
        self._state: dict[str, dict[str, object]] = {k: {} for k in keys}
        self._active = 0  # round-robin cursor into _api_keys

    def _throttle(self, key: str) -> None:
        with self._lock:
            now = time.time()
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            st = self._state[key]
            if st.get("day") != day:
                st["day"] = day
                st["calls"] = 0
                st["last"] = 0.0
            if int(st.get("calls") or 0) >= self.MAX_DAILY:
                return "exhausted"
            elapsed = now - float(st.get("last") or 0.0)
            if elapsed < self.MIN_INTERVAL_S:
                time.sleep(self.MIN_INTERVAL_S - elapsed)
            st["calls"] = int(st.get("calls") or 0) + 1
            st["last"] = time.time()
            return "ok"

    def _next_key(self) -> str:
        """Pick the next key that still has daily quota; None if all are spent."""
        with self._lock:
            for _ in range(len(self._api_keys)):
                key = self._api_keys[self._active % len(self._api_keys)]
                self._active += 1
                st = self._state.get(key, {})
                day = time.strftime("%Y-%m-%d")
                if st.get("day") == day and int(st.get("calls") or 0) >= self.MAX_DAILY:
                    continue
                return key
        return ""

    def _get(self, params: dict[str, str]) -> Any:
        """Run `params` against the keys, rotating off a key on 429 or daily-cap JSON."""
        last_err: str | None = None
        for _ in range(len(self._api_keys)):
            key = self._next_key()
            if not key:
                break
            if self._throttle(key) == "exhausted":
                continue
            query = urllib.parse.urlencode({"apikey": key, **params})
            url = f"{BASE}?{query}"
            try:
                data = self._request(url)
            except IngestError as e:
                if "429" in str(e) or "rate limited" in str(e):
                    self._burn_key(key)
                    last_err = str(e)
                    continue
                raise
            if self._daily_cap_message(data):
                # AV answers a spent key with HTTP 200 + a JSON daily-cap notice,
                # so treat it as exhausted and fall over to the next key.
                self._burn_key(key)
                last_err = "Alpha Vantage: daily request cap reached on this key"
                continue
            return data
        raise IngestError(last_err or "Alpha Vantage: all API keys exhausted")

    def _burn_key(self, key: str) -> None:
        """Mark a key as exhausted for the rest of the day."""
        with self._lock:
            st = self._state.setdefault(key, {})
            st["day"] = time.strftime("%Y-%m-%d")
            st["calls"] = self.MAX_DAILY

    @staticmethod
    def _daily_cap_message(data: Any) -> bool:
        """True when AV answered with its standard free-tier daily-cap notice."""
        if not isinstance(data, dict):
            return False
        msg = str(data.get("Note") or data.get("Information") or "")
        return "rate limit is 25 requests per day" in msg.lower()

    def _request(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": "finlink/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            if e.code == 429:
                raise IngestError(f"alphavantage: rate limited (429): {body}") from e
            raise IngestError(f"alphavantage: HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise IngestError(f"alphavantage: network error: {e.reason}") from e

    def fetch_prices(self, ticker: str, days: int = 400) -> list[PriceBar]:
        data = self._get(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": self._symbol(ticker),
                "outputsize": "compact",  # last ~100 days; full is premium
            }
        )
        series = data.get("Time Series (Daily)")
        if series is None:
            note = str(data.get("Note") or data.get("Information") or data)[:180]
            raise IngestError(f"{ticker}: Alpha Vantage returned no series: {note}")
        ccy = self._currency(ticker)

        directly_parsed = []
        for day_str, row in sorted(series.items()):
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue
            try:
                o = Decimal(str(row["1. open"]))
                h = Decimal(str(row["2. high"]))
                l = Decimal(str(row["3. low"]))
                c = Decimal(str(row["4. close"]))
                vol = int(float(row.get("5. volume") or 0))
            except (KeyError, ValueError, InvalidOperation) as e:
                raise IngestError(f"{ticker}: malformed row {day_str}: {e}") from e
            directly_parsed.append(
                PriceBar(
                    ticker=ticker, day=day, open=o, high=h, low=l, close=c,
                    adj_close=c,
                    volume=vol,
                    currency=ccy,
                )
            )
        if not directly_parsed:
            raise IngestError(f"{ticker}: Alpha Vantage returned no parseable rows")
        return directly_parsed[-min(days, len(directly_parsed)) :]

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        data = self._get({"function": "OVERVIEW", "symbol": self._symbol(ticker)})
        ccy = self._currency(ticker)
        metrics: dict[str, Decimal] = {}
        for fin_key, av_key in {
            "pe_ratio": "PERatio",
            "ps_ratio": "PriceToSalesRatioTTM",
            "pb_ratio": "PriceToBookRatio",
            "market_cap": "MarketCapitalization",
            "revenue_growth_yoy": "QuarterlyRevenueGrowthYOY",
            "gross_margin": "GrossProfitTTM",
            "operating_margin": "OperatingMarginTTM",
            "debt_to_equity": "DebtToEquityRatio",
        }.items():
            v = data.get(av_key)
            if v not in (None, "", "None"):
                try:
                    metrics[fin_key] = Decimal(str(v))
                except InvalidOperation:
                    pass
        return Fundamentals(
            ticker=ticker,
            currency=ccy,
            metrics=metrics,
            as_of=date.today().isoformat(),
        )

    # ------- helpers ---------------------------------------------------
    def _symbol(self, ticker: str) -> str:
        t = ticker.strip().upper()
        # Hong Kong: Alpha Vantage uses the 4-digit code with .HK suffix
        # (e.g. 0168.HK). Left-pad without introducing a leading zero.
        if t.endswith(".HK"):
            core = t[:-3].lstrip("0") or "0"
            return f"{core.zfill(4)}.HK"
        return t

    def _currency(self, ticker: str) -> str:
        if ticker.upper().endswith(".HK"):
            return "HKD"
        if ticker.upper().endswith(".ST"):
            return "SEK"
        return "USD"
