"""Deterministic mock driver — offline testing and CI.

Produces plausible data so pipelines can be exercised with zero network access.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from finlink.ingest.base import Fundamentals, IngestError, NewsItem, PriceBar

# Rough but non-zero anchors so weight/PnL maths is exercised meaningfully.
MOCK_ANCHORS = {
    "AAPL": (Decimal("180"), "USD"),
    "NVDA": (Decimal("120"), "USD"),
    "MSFT": (Decimal("400"), "USD"),
    "VOLV-B.ST": (Decimal("250"), "SEK"),
    "0700.HK": (Decimal("380"), "HKD"),
}


class MockMarketDriver:
    name = "mock"


    def __init__(self, anchors: dict[str, tuple[Decimal, str]] | None = None,
                 strict: bool = False) -> None:
        self._anchors = dict(MOCK_ANCHORS)
        if anchors:
            self._anchors.update(anchors)
        # strict=True makes unknown tickers fail like a real provider would.
        # Default keeps ad-hoc testing convenient; `onboard`/tests set strict.
        self.strict = strict

    def _anchor(self, ticker: str) -> tuple[Decimal, str]:
        if ticker in self._anchors:
            return self._anchors[ticker]
        if self.strict:
            raise IngestError(
                f"{ticker}: not in mock dataset. Add an anchor or use a real driver."
            )
        return (Decimal("100"), "USD")

    def fetch_prices(self, ticker: str, days: int = 400) -> list[PriceBar]:
        anchor, ccy = self._anchor(ticker)
        today = date.today()
        bars: list[PriceBar] = []
        for i in range(days, 0, -1):
            day = today - timedelta(days=i)
            # Deterministic gentle wave — no randomness, so tests are stable.
            drift = Decimal(str(round(1 + 0.05 * ((i % 30) / 30.0), 6)))
            close = (anchor * drift).quantize(Decimal("0.0001"))
            bars.append(
                PriceBar(
                    ticker=ticker,
                    day=day,
                    open=(close * Decimal("0.995")).quantize(Decimal("0.0001")),
                    high=(close * Decimal("1.01")).quantize(Decimal("0.0001")),
                    low=(close * Decimal("0.99")).quantize(Decimal("0.0001")),
                    close=close,
                    adj_close=close,
                    volume=1_000_000 + (i * 1000),
                    currency=ccy,
                )
            )
        return bars

    def fetch_fundamentals(self, ticker: str) -> Fundamentals:
        anchor, ccy = self._anchor(ticker)
        return Fundamentals(
            ticker=ticker,
            currency=ccy,
            as_of=date.today().isoformat(),
            metrics={
                "pe_ratio": Decimal("25.0"),
                "ps_ratio": Decimal("6.0"),
                "pb_ratio": Decimal("12.0"),
                "market_cap": anchor * Decimal("1e9"),
                "revenue_growth_yoy": Decimal("0.15"),
                "eps_growth_yoy": Decimal("0.18"),
                "gross_margin": Decimal("0.45"),
                "operating_margin": Decimal("0.30"),
                "debt_to_equity": Decimal("0.8"),
                "free_cash_flow": Decimal("5e9"),
            },
        )


class MockNewsDriver:
    name = "mock"

    def fetch_news(self, ticker: str, limit: int = 30) -> list[NewsItem]:
        today = date.today()
        return [
            NewsItem(
                ticker=ticker,
                title=f"{ticker} reports quarterly results",
                url=f"https://example.com/{ticker}/q-{i}",
                published_at=(today - timedelta(days=i)).isoformat(),
                source="mock",
                summary=f"Mock summary {i} for {ticker}.",
            )
            for i in range(min(limit, 5))
        ]
