from __future__ import annotations

from decimal import Decimal

import pytest

from finlink.domain.pnl import Lot, Position


def mk(ticker: str = "AAPL", ccy: str = "USD") -> Position:
    return Position(ticker=ticker, currency=ccy)


def test_buy_then_partial_sell_realised_pnl():
    """The plan's canonical check: buy 10 @100, sell 4 @120."""
    pos = mk()
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100")))
    realised = pos.reduce(Decimal("4"), Decimal("120"))

    assert realised == Decimal("80")  # 4 * (120-100)
    assert pos.quantity == Decimal("6")
    assert pos.cost_basis == Decimal("600")
    assert pos.avg_cost == Decimal("100")
    assert pos.realised_pnl == Decimal("80")


def test_sell_across_two_lots_fifo():
    pos = mk()
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100")))
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("120")))
    assert pos.avg_cost == Decimal("110")

    realised = pos.reduce(Decimal("15"), Decimal("130"))
    # FIFO: 10 @100 + 5 @120 sold -> cost 1000 + 600 = 1600; proceeds 1950
    assert realised == Decimal("350")
    assert pos.quantity == Decimal("5")
    assert pos.cost_basis == Decimal("600")  # remaining 5 from the 120 lot


def test_fees_included_in_cost_basis():
    pos = mk()
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), fees=Decimal("20")))
    assert pos.cost_basis == Decimal("1020")
    realised = pos.reduce(Decimal("10"), Decimal("110"), fees=Decimal("5"))
    assert realised == Decimal("75")  # proceeds 1100-5=1095, cost 1020


def test_unrealised_pnl_and_market_value():
    pos = mk()
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100")))
    assert pos.market_value(Decimal("150")) == Decimal("1500")
    assert pos.unrealised_pnl(Decimal("150")) == Decimal("500")


def test_oversell_rejected():
    pos = mk()
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100")))
    with pytest.raises(ValueError, match="only 10 held"):
        pos.reduce(Decimal("11"), Decimal("100"))


def test_currency_mismatch_rejected():
    pos = mk(ccy="USD")
    with pytest.raises(ValueError, match="currency mismatch"):
        pos.add_lot(Lot(quantity=Decimal("1"), unit_cost=Decimal("1"), currency="SEK"))


def test_empty_position_has_zero_avg_cost():
    pos = mk()
    assert pos.avg_cost == Decimal("0")
    assert pos.unrealised_pnl(Decimal("100")) == Decimal("0")


def test_no_float_leakage():
    """Money must be Decimal end-to-end; floats are forbidden in financial code."""
    pos = mk()
    pos.add_lot(Lot(quantity=Decimal("3"), unit_cost=Decimal("10.10")))
    assert isinstance(pos.cost_basis, Decimal)
    assert pos.cost_basis == Decimal("30.30")
