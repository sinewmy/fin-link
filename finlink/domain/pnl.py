"""Cost basis, realised and unrealised PnL. Pure functions, Decimal-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True)
class Lot:
    """One opening (or adding) trade, in its source currency."""

    quantity: Decimal
    unit_cost: Decimal
    currency: str = "USD"
    opened_at: str = ""
    fees: Decimal = ZERO

    @property
    def cost_basis(self) -> Decimal:
        return self.quantity * self.unit_cost + self.fees


@dataclass
class Position:
    """Aggregated holding for one ticker. Mutated only via add_lot/reduce."""

    ticker: str
    currency: str
    lots: list[Lot] = field(default_factory=list)
    realised_pnl: Decimal = ZERO

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.lots), ZERO)

    @property
    def cost_basis(self) -> Decimal:
        """Cost basis of the *remaining* quantity."""
        return sum((lot.cost_basis for lot in self.lots if lot.quantity > 0), ZERO)

    @property
    def avg_cost(self) -> Decimal:
        qty = self.quantity
        if qty == 0:
            return ZERO
        return self.cost_basis / qty

    def add_lot(self, lot: Lot) -> None:
        if lot.currency != self.currency:
            raise ValueError(
                f"currency mismatch for {self.ticker}: position {self.currency}, lot {lot.currency}"
            )
        if lot.quantity <= 0:
            raise ValueError("lot quantity must be positive")
        self.lots.append(lot)

    def reduce(self, quantity: Decimal, unit_price: Decimal, fees: Decimal = ZERO) -> Decimal:
        """Sell `quantity` FIFO. Returns realised PnL for this sale (source currency)."""
        if quantity <= 0:
            raise ValueError("sell quantity must be positive")
        if quantity > self.quantity:
            raise ValueError(f"cannot sell {quantity} {self.ticker}: only {self.quantity} held")

        remaining = quantity
        proceeds = quantity * unit_price - fees
        cost_of_sold = ZERO

        for lot in self.lots:
            if remaining <= 0:
                break
            if lot.quantity <= 0:
                continue
            take = min(lot.quantity, remaining)
            # Cost basis is proportional; fees are amortised across the lot.
            lot_cost_per_unit = lot.cost_basis / lot.quantity
            cost_of_sold += take * lot_cost_per_unit
            object.__setattr__(lot, "quantity", lot.quantity - take)
            remaining -= take

        self.lots = [lot for lot in self.lots if lot.quantity > 0]
        realised = proceeds - cost_of_sold
        self.realised_pnl += realised
        return realised

    def unrealised_pnl(self, market_price: Decimal) -> Decimal:
        qty = self.quantity
        if qty == 0:
            return ZERO
        return qty * market_price - self.cost_basis

    def market_value(self, market_price: Decimal) -> Decimal:
        return self.quantity * market_price
