"""Weight drift and behavioural patterns (P4 portfolio + individual halves).

Pure functions over the ledger and cached prices. Every figure a review reports
originates here; the model only interprets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from finlink.domain.money import q, to_usd
from finlink.domain.pnl import Position
from finlink.models import LedgerRow, Side

ZERO = Decimal("0")
PCT = Decimal("0.01")


@dataclass(frozen=True)
class WeightSnapshot:
    """Weights at a point in time, keyed by ticker and by sector."""

    as_of: date
    by_ticker: dict[str, Decimal]
    by_sector: dict[str, Decimal]
    by_country: dict[str, Decimal]
    cash_pct: Decimal
    total_usd: Decimal


def snapshot(
    positions: dict[str, Position],
    prices: dict[str, Decimal],
    cash: dict[str, Decimal],
    fx: dict[str, Decimal],
    as_of: date,
    sectors: dict[str, str] | None = None,
    countries: dict[str, str] | None = None,
) -> WeightSnapshot:
    """Value a set of positions at given prices and express them as weights."""
    sectors, countries = sectors or {}, countries or {}
    by_ticker: dict[str, Decimal] = {}
    by_sector: dict[str, Decimal] = {}
    by_country: dict[str, Decimal] = {}
    total = ZERO
    values: dict[str, Decimal] = {}

    for ticker, pos in positions.items():
        price = prices.get(ticker)
        if price is None or pos.quantity <= 0:
            continue
        value = to_usd(pos.market_value(price), pos.currency, fx)
        values[ticker] = value
        total += value

    cash_usd = sum((to_usd(amt, ccy, fx) for ccy, amt in cash.items()), ZERO)
    total += cash_usd
    if total == 0:
        return WeightSnapshot(as_of, {}, {}, {}, ZERO, ZERO)

    for ticker, value in values.items():
        weight = value / total * 100
        by_ticker[ticker] = weight
        sector = sectors.get(ticker.upper(), "Unclassified")
        by_sector[sector] = by_sector.get(sector, ZERO) + weight
        country = countries.get(ticker.upper(), "Unknown")
        by_country[country] = by_country.get(country, ZERO) + weight
    return WeightSnapshot(as_of, by_ticker, by_sector, by_country, cash_usd / total * 100, total)


@dataclass(frozen=True)
class Drift:
    name: str
    start_pct: Decimal
    end_pct: Decimal

    @property
    def change_pct(self) -> Decimal:
        return self.end_pct - self.start_pct


def diff(start: WeightSnapshot, end: WeightSnapshot) -> dict[str, list[Drift]]:
    """Drift per position, sector and country between two snapshots."""
    out: dict[str, list[Drift]] = {"position": [], "sector": [], "country": []}
    for kind, a, b in (
        ("position", start.by_ticker, end.by_ticker),
        ("sector", start.by_sector, end.by_sector),
        ("country", start.by_country, end.by_country),
    ):
        for name in sorted(set(a) | set(b)):
            out[kind].append(Drift(name, a.get(name, ZERO), b.get(name, ZERO)))
        out[kind].sort(key=lambda d: abs(d.change_pct), reverse=True)
    return out


# --------------------------------------------------------------- behaviour patterns


@dataclass(frozen=True)
class RoundTrip:
    """One buy matched with a later sell of the same ticker."""

    ticker: str
    bought: date
    sold: date
    quantity: Decimal
    pnl_pct: Decimal | None

    @property
    def days_held(self) -> int:
        return (self.sold - self.bought).days


def round_trips(ledger: list[LedgerRow]) -> list[RoundTrip]:
    """FIFO-match sells against earlier buys to measure holding periods."""
    open_lots: dict[str, list[tuple[date, Decimal, Decimal]]] = {}
    out: list[RoundTrip] = []
    for row in sorted(ledger, key=lambda r: (r.date, r.ticker)):
        if row.side is Side.BUY:
            open_lots.setdefault(row.ticker, []).append((row.date, row.quantity, row.price))
        else:
            lots = open_lots.get(row.ticker, [])
            remaining = row.quantity
            while remaining > 0 and lots:
                bought, qty, price = lots[0]
                take = min(remaining, qty)
                pnl_pct = ((row.price - price) / price * 100) if price else None
                out.append(
                    RoundTrip(
                        ticker=row.ticker,
                        bought=bought,
                        sold=row.date,
                        quantity=take,
                        pnl_pct=pnl_pct,
                    )
                )
                remaining -= take
                if take >= qty:
                    lots.pop(0)
                else:
                    lots[0] = (bought, qty - take, price)
    return out


@dataclass(frozen=True)
class Pattern:
    """A concrete, countable behavioural observation — never a vague judgement."""

    name: str
    detail: str
    evidence: tuple[str, ...]


QUICK_SELL_DAYS = 14


def detect_patterns(
    ledger: list[LedgerRow],
    *,
    quick_sell_days: int = QUICK_SELL_DAYS,
    as_of: date | None = None,
) -> list[Pattern]:
    """Surface recurring behavioural patterns from the user's own history.

    Every pattern is countable and cites the trades behind it, so it can be checked
    rather than taken on faith.
    """
    as_of = as_of or date.today()
    patterns: list[Pattern] = []

    trips = round_trips(ledger)
    if trips:
        quick = [t for t in trips if t.days_held <= quick_sell_days]
        if quick:
            patterns.append(
                Pattern(
                    name=f"sells within {quick_sell_days} days of purchase",
                    detail=(
                        f"{len(quick)} of {len(trips)} round trips were closed within "
                        f"{quick_sell_days} days of the buy"
                    ),
                    evidence=tuple(
                        f"{t.ticker} bought {t.bought.isoformat()} sold {t.sold.isoformat()} "
                        f"({t.days_held}d)"
                        for t in quick
                    ),
                )
            )
        losers = [t for t in trips if t.pnl_pct is not None and t.pnl_pct < 0]
        winners = [t for t in trips if t.pnl_pct is not None and t.pnl_pct > 0]
        if losers and winners:
            avg_loss_days = sum(t.days_held for t in losers) / len(losers)
            avg_win_days = sum(t.days_held for t in winners) / len(winners)
            if avg_loss_days < avg_win_days:
                patterns.append(
                    Pattern(
                        name="losses closed faster than winners",
                        detail=(
                            f"average holding period: {avg_loss_days:.0f}d on losing trades vs "
                            f"{avg_win_days:.0f}d on winning trades"
                        ),
                        evidence=tuple(
                            f"{t.ticker} {t.days_held}d pnl "
                            f"{q(t.pnl_pct, PCT) if t.pnl_pct is not None else '?'}%"
                            for t in sorted(trips, key=lambda x: x.days_held)
                        ),
                    )
                )

    buys = [r for r in ledger if r.side is Side.BUY]
    if buys:
        no_reason = [r for r in buys if not r.reason.strip()]
        if no_reason:
            patterns.append(
                Pattern(
                    name="buys recorded without a reason",
                    detail=f"{len(no_reason)} of {len(buys)} buys have no stated reason",
                    evidence=tuple(f"{r.ticker} {r.date.isoformat()}" for r in no_reason[:5]),
                )
            )
        # Buys clustered into a short window — a burst, not a plan.
        burst: list[LedgerRow] = []
        for r in sorted(buys, key=lambda x: x.date):
            if not burst or (r.date - burst[0].date).days <= 7:
                burst.append(r)
            else:
                if len(burst) >= 3:
                    patterns.append(
                        Pattern(
                            name="buying burst",
                            detail=(
                                f"{len(burst)} buys within 7 days starting "
                                f"{burst[0].date.isoformat()}"
                            ),
                            evidence=tuple(f"{b.ticker} {b.date.isoformat()}" for b in burst),
                        )
                    )
                burst = [r]
        if len(burst) >= 3:
            patterns.append(
                Pattern(
                    name="buying burst",
                    detail=(
                        f"{len(burst)} buys within 7 days starting {burst[0].date.isoformat()}"
                    ),
                    evidence=tuple(f"{b.ticker} {b.date.isoformat()}" for b in burst),
                )
            )

    stale = [r for r in ledger if (as_of - r.date).days > 365]
    if stale:
        patterns.append(
            Pattern(
                name="old unreviewed trades",
                detail=f"{len(stale)} trades are older than a year",
                evidence=tuple(f"{r.ticker} {r.date.isoformat()}" for r in stale[:5]),
            )
        )
    return patterns


def week_range(week: str) -> tuple[date, date]:
    """ISO week 'YYYY-Www' -> (Monday, Sunday). Raises on a bad week string."""
    try:
        year, wk = week.upper().split("-W")
        monday = date.fromisocalendar(int(year), int(wk), 1)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"invalid week {week!r}: expected e.g. 2026-W37") from e
    return monday, monday + timedelta(days=6)


def current_week(today: date | None = None) -> str:
    today = today or date.today()
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"
