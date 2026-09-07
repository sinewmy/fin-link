"""Portfolio-level statistics: correlation, covariance, volatility.

Pure Decimal maths over aligned return series — no numpy, no floats, no LLM.
Kept separate from `quant.py` (single-name metrics) so the two stay testable apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finlink.domain.quant import _sqrt, daily_returns
from finlink.ingest.base import PriceBar

ZERO = Decimal("0")
ONE = Decimal("1")
TRADING_DAYS = 252


@dataclass(frozen=True)
class Correlation:
    a: str
    b: str
    coefficient: Decimal
    overlap_days: int

    @property
    def reliable(self) -> bool:
        """Too few overlapping observations makes a correlation meaningless."""
        return self.overlap_days >= MIN_OVERLAP


MIN_OVERLAP = 20


def align_series(
    series: dict[str, list[PriceBar]],
) -> tuple[list[str], dict[str, dict[str, Decimal]]]:
    """Align several price series onto the dates they share.

    Returns (tickers, {ticker: {day: return}}). Tickers with no overlap at all are
    dropped rather than silently correlated as zero.
    """
    rets: dict[str, dict[str, Decimal]] = {}
    for ticker, bars in series.items():
        by_day: dict[str, Decimal] = {}
        closes = {b.day.isoformat(): b.close for b in bars}
        for prev, cur in zip(bars, bars[1:], strict=False):
            if prev.close == 0:
                continue
            by_day[cur.day.isoformat()] = (cur.close - prev.close) / prev.close
        _ = closes  # closes kept for clarity of intent; returns are what we align on
        if by_day:
            rets[ticker] = by_day

    if not rets:
        return [], {}

    common = (
        set.intersection(*(set(v) for v in rets.values()))
        if len(rets) > 1
        else set(next(iter(rets.values())))
    )
    if not common:
        return [], {}
    return sorted(rets), {t: {d: v for d, v in rets[t].items() if d in common} for t in rets}


def correlation_matrix(series: dict[str, list[PriceBar]]) -> list[Correlation]:
    """Pairwise Pearson correlation of daily returns, over shared dates only."""
    tickers, aligned = align_series(series)
    out: list[Correlation] = []
    days = sorted(next(iter(aligned.values())).keys()) if aligned else []
    for i, a in enumerate(tickers):
        for b in tickers[i:]:
            ra = [aligned[a][d] for d in days]
            rb = [aligned[b][d] for d in days]
            if a == b:
                out.append(Correlation(a, b, ONE, len(ra)))
                continue
            if len(ra) < 2:
                # Report the pair as unreliable rather than dropping it: "not enough
                # data" is a different statement from "no relationship".
                out.append(Correlation(a, b, ZERO, len(ra)))
                continue
            out.append(Correlation(a, b, pearson(ra, rb), len(ra)))
    return out


def pearson(xs: list[Decimal], ys: list[Decimal]) -> Decimal:
    n = Decimal(len(xs))
    if n < 2:
        return ZERO
    mx = sum(xs, ZERO) / n
    my = sum(ys, ZERO) / n
    cov = sum(((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)), ZERO)
    var_x = sum(((x - mx) ** 2 for x in xs), ZERO)
    var_y = sum(((y - my) ** 2 for y in ys), ZERO)
    if var_x == 0 or var_y == 0:
        # A constant series has no variance; correlation is undefined, not zero.
        return ZERO
    denom = _sqrt(var_x * var_y)
    if denom == 0:
        return ZERO
    return cov / denom


def portfolio_return_series(
    series: dict[str, list[PriceBar]], weights: dict[str, Decimal]
) -> list[Decimal]:
    """Weighted daily return series for the whole portfolio.

    Weights are fractions of portfolio value (0-1). Missing days are skipped, so a
    name listed later than another does not distort the early part of the series.
    """
    tickers, aligned = align_series(series)
    if not aligned:
        return []
    days = sorted(next(iter(aligned.values())).keys())
    total_weight = sum((weights.get(t, ZERO) for t in tickers), ZERO)
    if total_weight == 0:
        return []
    out: list[Decimal] = []
    for d in days:
        acc = ZERO
        used = ZERO
        for t in tickers:
            w = weights.get(t, ZERO)
            if w == 0:
                continue
            r = aligned[t].get(d)
            if r is None:
                continue
            acc += w * r
            used += w
        if used == 0:
            continue
        out.append(acc / used)
    return out


def portfolio_volatility(returns: list[Decimal]) -> Decimal:
    """Annualised standard deviation of a return series, in percent."""
    if len(returns) < 2:
        return ZERO
    n = Decimal(len(returns))
    mean = sum(returns, ZERO) / n
    variance = sum(((r - mean) ** 2 for r in returns), ZERO) / (n - 1)
    if variance == 0:
        return ZERO
    return _sqrt(variance) * _sqrt(Decimal(TRADING_DAYS)) * 100


def portfolio_drawdown(returns: list[Decimal]) -> Decimal:
    """Max drawdown of a portfolio return series, as a negative percent."""
    if not returns:
        return ZERO
    level = ONE
    peak = ONE
    worst = ZERO
    for r in returns:
        level *= ONE + r
        if level > peak:
            peak = level
        if peak > 0:
            dd = (level - peak) / peak * 100
            if dd < worst:
                worst = dd
    return worst


def annualised_return(returns: list[Decimal]) -> Decimal:
    """Cumulative compounded return, as a percent."""
    if not returns:
        return ZERO
    level = ONE
    for r in returns:
        level *= ONE + r
    return (level - ONE) * 100


def daily_returns_for(bars: list[PriceBar]) -> list[Decimal]:
    return daily_returns(bars)
