"""Deterministic quantitative metrics. Computed in code, never by the LLM.

Every number a report shows must originate here (TECHNICAL_DESIGN §4.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finlink.ingest.base import PriceBar

ZERO = Decimal("0")
ONE = Decimal("1")
TRADING_DAYS = 252


@dataclass(frozen=True)
class QuantSummary:
    ticker: str
    currency: str
    last_price: Decimal
    as_of: str
    period_return_pct: Decimal
    annualised_vol_pct: Decimal
    max_drawdown_pct: Decimal
    sma_50: Decimal | None
    sma_200: Decimal | None


def _closes(bars: list[PriceBar]) -> list[Decimal]:
    return [b.close for b in bars]


def simple_return(bars: list[PriceBar]) -> Decimal:
    """Total return over the window."""
    closes = _closes(bars)
    if len(closes) < 2 or closes[0] == 0:
        return ZERO
    return (closes[-1] - closes[0]) / closes[0] * 100


def daily_returns(bars: list[PriceBar]) -> list[Decimal]:
    closes = _closes(bars)
    out: list[Decimal] = []
    for prev, cur in zip(closes, closes[1:], strict=False):
        if prev == 0:
            continue
        out.append((cur - prev) / prev)
    return out


def annualised_volatility(bars: list[PriceBar]) -> Decimal:
    """Sample stdev of daily returns * sqrt(252), in percent."""
    rets = daily_returns(bars)
    if len(rets) < 2:
        return ZERO
    mean = sum(rets, ZERO) / len(rets)
    variance = sum(((r - mean) ** 2 for r in rets), ZERO) / (len(rets) - 1)
    # Newton's method for sqrt — Decimal has no direct sqrt with this precision need
    if variance == 0:
        return ZERO
    stdev = _sqrt(variance)
    return stdev * _sqrt(Decimal(TRADING_DAYS)) * 100


def max_drawdown(bars: list[PriceBar]) -> Decimal:
    """Largest peak-to-trough decline, as a negative percent."""
    closes = _closes(bars)
    if not closes:
        return ZERO
    peak = closes[0]
    worst = ZERO
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = (c - peak) / peak * 100
            if dd < worst:
                worst = dd
    return worst


def sma(bars: list[PriceBar], window: int) -> Decimal | None:
    closes = _closes(bars)
    if len(closes) < window or window <= 0:
        return None
    return sum(closes[-window:], ZERO) / window


def _sqrt(value: Decimal, iterations: int = 30) -> Decimal:
    if value < 0:
        raise ValueError(f"cannot sqrt negative: {value}")
    if value == 0:
        return ZERO
    guess = value / 2
    for _ in range(iterations):
        guess = (guess + value / guess) / 2
    return guess


def summarise(bars: list[PriceBar]) -> QuantSummary | None:
    if not bars:
        return None
    last = bars[-1]
    return QuantSummary(
        ticker=last.ticker,
        currency=last.currency,
        last_price=last.close,
        as_of=last.day.isoformat(),
        period_return_pct=simple_return(bars),
        annualised_vol_pct=annualised_volatility(bars),
        max_drawdown_pct=max_drawdown(bars),
        sma_50=sma(bars, 50),
        sma_200=sma(bars, 200),
    )


def valuation_metrics(fundamentals_metrics: dict[str, Decimal]) -> dict[str, Decimal]:
    """Pass through with canonical names. No computation that the LLM could fake."""
    return dict(fundamentals_metrics)
