"""Portfolio aggregation: positions, weights, concentration, cash.

Pure functions over already-parsed data. No I/O, no LLM, no market data fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finlink.domain.money import to_usd
from finlink.domain.pnl import Position

ZERO = Decimal("0")
HKD_PEG = Decimal("7.8")  # HKD per 1 USD (market convention)


@dataclass(frozen=True)
class ValuedPosition:
    ticker: str
    quantity: Decimal
    avg_cost: Decimal
    currency: str
    price: Decimal
    market_value_local: Decimal
    market_value_usd: Decimal
    cost_basis_usd: Decimal
    unrealised_pnl_usd: Decimal
    weight_pct: Decimal
    thesis_slug: str | None


@dataclass(frozen=True)
class PortfolioView:
    positions: tuple[ValuedPosition, ...]
    cash_usd: Decimal
    total_value_usd: Decimal
    positions_value_usd: Decimal
    unrealised_pnl_usd: Decimal

    @property
    def cash_pct(self) -> Decimal:
        if self.total_value_usd == 0:
            return ZERO
        return self.cash_usd / self.total_value_usd * 100

    def weight_of(self, ticker: str) -> Decimal:
        for p in self.positions:
            if p.ticker == ticker:
                return p.weight_pct
        return ZERO


def build_rates(
    fx: dict[str, Decimal] | None = None, hkd_peg: Decimal = HKD_PEG
) -> dict[str, Decimal]:
    """FX rates expressed as USD per 1 unit of foreign currency.

    Convention (single, enforced everywhere): rate["SEK"] = 0.095 means 1 SEK = 0.095 USD.
    HKD is quoted the other way round in the market (7.8 HKD per 1 USD), so the peg must be
    inverted here — storing 7.8 directly would make 1 HKD worth 7.8 USD.
    """
    if hkd_peg <= 0:
        raise ValueError(f"invalid HKD peg: {hkd_peg}")
    rates = {"HKD": Decimal("1") / hkd_peg}  # 1 HKD = 1/7.8 USD
    if fx:
        rates.update({k.upper(): Decimal(str(v)) for k, v in fx.items()})
    return rates


def value_positions(
    positions: dict[str, Position],
    prices: dict[str, Decimal],
    cash: dict[str, Decimal],
    fx: dict[str, Decimal] | None = None,
) -> PortfolioView:
    """Value every position in USD and compute weights.

    Raises FXError if a non-USD currency has no rate — never silently treats it as 1.0.
    """
    rates = build_rates(fx)

    valued: list[ValuedPosition] = []
    for ticker, pos in positions.items():
        if pos.quantity <= 0:
            continue
        price = prices.get(ticker)
        if price is None:
            raise ValueError(f"missing price for {ticker}; run `finlink ingest`")
        mv_local = pos.market_value(price)
        mv_usd = to_usd(mv_local, pos.currency, rates)
        cb_usd = to_usd(pos.cost_basis, pos.currency, rates)
        valued.append(
            ValuedPosition(
                ticker=ticker,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                currency=pos.currency,
                price=price,
                market_value_local=mv_local,
                market_value_usd=mv_usd,
                cost_basis_usd=cb_usd,
                unrealised_pnl_usd=mv_usd - cb_usd,
                weight_pct=ZERO,  # filled below
                thesis_slug=None,
            )
        )

    cash_usd = sum((to_usd(amt, ccy, rates) for ccy, amt in cash.items()), ZERO)
    positions_value_usd = sum((p.market_value_usd for p in valued), ZERO)
    total = positions_value_usd + cash_usd

    if total == 0:
        raise ValueError("portfolio total value is zero; check positions and cash")

    valued = [
        ValuedPosition(**{**vars(p), "weight_pct": p.market_value_usd / total * 100})
        for p in valued
    ]
    return PortfolioView(
        positions=tuple(valued),
        cash_usd=cash_usd,
        total_value_usd=total,
        positions_value_usd=positions_value_usd,
        unrealised_pnl_usd=sum((p.unrealised_pnl_usd for p in valued), ZERO),
    )


def fx_exposure(view: PortfolioView) -> dict[str, Decimal]:
    """Market value by source currency (USD-normalised)."""
    out: dict[str, Decimal] = {}
    for p in view.positions:
        out[p.currency] = out.get(p.currency, ZERO) + p.market_value_usd
    return out
