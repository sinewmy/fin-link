"""Akshare market-data driver for US and Hong Kong stocks.

Akshare is a free, community-maintained library with excellent coverage for:
  - US stocks (via stock_us_daily)
  - Hong Kong stocks (via stock_hk_daily)

No API key required. No rate limits on free tier.

Ticker conventions:
  US:    AAPL, NVDA, MSFT  (uppercase, no suffix)
  HK:    0700.HK, 00981.HK (5-digit code with .HK suffix; akshare uses bare 5-digit form)

Limitations:
  - No fundamentals (balance sheet, P/E, etc.) — only price data
  - No Swedish stocks (.ST), European, or other exchanges
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from finlink.ingest.base import Fundamentals, IngestError, PriceBar


class AkshareDriver:
    """Akshare driver: free, no-key market data for US and HK stocks."""

    name = "akshare"

    def __init__(self) -> None:
        """Initialize the akshare driver. No API key needed."""
        try:
            import akshare as ak  # noqa: F401 - verify import on init
        except ImportError:
            raise IngestError(
                "akshare library not installed. Install with: pip install akshare"
            ) from None

    def fetch_prices(self, ticker: str, days: int = 400) -> list[PriceBar]:
        """Fetch historical daily OHLC data.

        Args:
            ticker: US ticker (e.g. 'AAPL') or HK ticker (e.g. '0700.HK')
            days: number of days to fetch (best-effort; may return fewer)

        Returns:
            List of PriceBar, oldest to newest

        Raises:
            IngestError: if ticker not found or akshare fails
        """
        import akshare as ak

        ticker = ticker.strip().upper()

        try:
            if ticker.endswith(".HK"):
                # Hong Kong: 0700.HK -> "00700", 00981.HK -> "00981"
                hk_code = self._hk_symbol(ticker)
                df = ak.stock_hk_daily(symbol=hk_code)
                ccy = "HKD"
            else:
                # US: AAPL -> "AAPL"
                df = ak.stock_us_daily(symbol=ticker)
                ccy = "USD"
        except Exception as e:  # noqa: BLE001
            raise IngestError(f"{ticker}: akshare failed to fetch data: {e}") from e

        if df is None or df.empty:
            raise IngestError(f"{ticker}: akshare returned no data")

        # akshare returns columns like: date, open, close, high, low, volume
        # (column names may vary; handle both cases)
        bars: list[PriceBar] = []
        try:
            for _, row in df.iterrows():
                # Handle both snake_case and other naming conventions
                day_val = row.get("date") or row.get("日期")
                if day_val is None:
                    continue

                # Parse date — could be string or already a date object
                if isinstance(day_val, str):
                    day = date.fromisoformat(day_val.split()[0])  # strip time if present
                else:
                    day = day_val.date() if hasattr(day_val, "date") else day_val

                # Extract OHLC; handle both English and Chinese column names
                o = Decimal(
                    str(row.get("open") or row.get("开盘价") or row.get("o") or 0)
                )
                h = Decimal(
                    str(row.get("high") or row.get("最高价") or row.get("h") or 0)
                )
                l = Decimal(str(row.get("low") or row.get("最低价") or row.get("l") or 0))
                c = Decimal(str(row.get("close") or row.get("收盘价") or row.get("c") or 0))
                vol = int(float(row.get("volume") or row.get("成交量") or row.get("v") or 0))

                if o == 0 or c == 0:  # skip incomplete rows
                    continue

                bars.append(
                    PriceBar(
                        ticker=ticker,
                        day=day,
                        open=o,
                        high=h,
                        low=l,
                        close=c,
                        adj_close=c,  # akshare doesn't provide adj_close; use close
                        volume=vol,
                        currency=ccy,
                    )
                )
        except (KeyError, ValueError, InvalidOperation) as e:
            raise IngestError(f"{ticker}: malformed row in akshare data: {e}") from e

        if not bars:
            raise IngestError(f"{ticker}: akshare returned no parseable rows")

        # Return the most recent `days` entries
        return bars[-min(days, len(bars)) :]

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        """Akshare does not provide fundamentals.

        Returns a minimal Fundamentals object with empty metrics.
        This allows the ingest pipeline to proceed; users relying on fundamentals
        must use a different provider or accept missing metrics.
        """
        ticker = ticker.strip().upper()
        ccy = "HKD" if ticker.endswith(".HK") else "USD"

        return Fundamentals(
            ticker=ticker,
            currency=ccy,
            metrics={},  # akshare does not provide fundamentals
            as_of=date.today().isoformat(),
        )

    @staticmethod
    def _hk_symbol(ticker: str) -> str:
        """Convert fin-link HK ticker to akshare format.

        fin-link: 0700.HK, 00981.HK, 01810.HK
        akshare:  00700,   00981,     01810   (5-digit zero-padded)

        Args:
            ticker: ticker with .HK suffix (e.g. '0700.HK')

        Returns:
            5-digit zero-padded code (e.g. '00700')
        """
        code = ticker[:-3].lstrip("0") or "0"  # strip .HK, remove leading zeros
        return code.zfill(5)  # pad to 5 digits
