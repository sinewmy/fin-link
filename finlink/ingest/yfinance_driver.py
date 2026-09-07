"""Yahoo Finance driver (via yfinance).

Coverage caveat: Yahoo's HK (and A-share) coverage is incomplete. Large caps work,
smaller names can return nulls. `onboard` verifies every ticker and FAILS LOUD
rather than storing nulls — silently persisting a null price would corrupt weights.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf  # type: ignore[import-untyped]

from finlink.ingest.base import Fundamentals, IngestError, PriceBar

SUFFIX_BY_EXCHANGE = {"US": "", "STO": ".ST", "HKG": ".HK"}


class YFinanceDriver:
    name = "yfinance"

    def __init__(self, session: object | None = None) -> None:
        self._session = session

    def _ticker(self, ticker: str) -> Any:
        return yf.Ticker(ticker, session=self._session) if self._session else yf.Ticker(ticker)

    def fetch_prices(self, ticker: str, days: int = 400) -> list[PriceBar]:
        t = self._ticker(ticker)
        period = f"{max(days // 250 + 1, 1)}y" if days > 250 else f"{days}d"
        try:
            df = t.history(period=period, auto_adjust=False)
        except Exception as e:  # noqa: BLE001 - yfinance raises many types
            raise IngestError(f"{ticker}: price fetch failed: {e}") from e

        if df is None or df.empty:
            raise IngestError(
                f"{ticker}: no price data returned. Either the ticker is wrong, or "
                f"Yahoo does not cover this market (HK and some SE names have gaps)."
            )

        currency = self._detect_currency(t, ticker)
        bars: list[PriceBar] = []
        for idx, row in df.iterrows():
            day = idx.date() if hasattr(idx, "date") else idx

            def dec(col: str, _row: object = row, _day: object = day) -> Decimal:
                # Bound as default args: a plain closure would capture the loop
                # variables and every bar would report the last row's value.
                val = _row.get(col)  # type: ignore[attr-defined]
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    raise IngestError(
                        f"{ticker}: null {col} on {_day}; refusing to store partial data"
                    )
                return Decimal(str(val))

            bars.append(
                PriceBar(
                    ticker=ticker,
                    day=day,
                    open=dec("Open"),
                    high=dec("High"),
                    low=dec("Low"),
                    close=dec("Close"),
                    adj_close=(
                        dec("Adj Close")
                        if row.get("Adj Close") is not None and not pd.isna(row.get("Adj Close"))
                        else dec("Close")
                    ),
                    volume=int(row.get("Volume") or 0),
                    currency=currency,
                )
            )
        return bars

    def _detect_currency(self, t: object, ticker: str) -> str:
        for attr in ("fast_info", "info"):
            try:
                info = getattr(t, attr, None)
                ccy = getattr(info, "currency", None) if info is not None else None
                if ccy:
                    return str(ccy).upper()
            except Exception:  # noqa: BLE001 - fast_info can raise on uncovered tickers
                continue
        # Fall back to the suffix; better an explicit guess than a silent null.
        if ticker.upper().endswith(".HK"):
            return "HKD"
        if ticker.upper().endswith(".ST"):
            return "SEK"
        return "USD"

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        t = self._ticker(ticker)
        try:
            info = t.info or {}
        except Exception as e:  # noqa: BLE001
            raise IngestError(f"{ticker}: fundamentals fetch failed: {e}") from e

        def num(key: str) -> Decimal | None:
            v = info.get(key)
            if v is None:
                return None
            try:
                return Decimal(str(v))
            except Exception:  # noqa: BLE001
                return None

        mapping = {
            "pe_ratio": "trailingPE",
            "ps_ratio": "priceToSalesTrailing12Months",
            "pb_ratio": "priceToBook",
            "market_cap": "marketCap",
            "revenue_growth_yoy": "revenueGrowth",
            "eps_growth_yoy": "earningsGrowth",
            "gross_margin": "grossMargins",
            "operating_margin": "operatingMargins",
            "debt_to_equity": "debtToEquity",
            "free_cash_flow": "freeCashflow",
        }
        metrics = {k: v for k, key in mapping.items() if (v := num(key)) is not None}
        return Fundamentals(
            ticker=ticker,
            currency=str(info.get("currency") or self._detect_currency(t, ticker)).upper(),
            as_of=datetime.now(UTC).date().isoformat(),
            metrics=metrics,
        )
