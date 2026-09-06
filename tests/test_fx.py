from __future__ import annotations

from decimal import Decimal

import pytest

from finlink.domain.money import FXError, q, to_usd
from finlink.domain.pnl import Lot, Position
from finlink.domain.portfolio import build_rates, fx_exposure, value_positions


def test_usd_identity():
    assert to_usd(Decimal("100"), "USD", {}) == Decimal("100")


def test_sek_conversion():
    assert to_usd(Decimal("1000"), "SEK", {"SEK": Decimal("0.095")}) == Decimal("95")


def test_hkd_peg_means_78_hkd_per_usd():
    """7.8 HKD = 1 USD. Regression guard: storing the peg un-inverted inflates HKD 60x."""
    rates = build_rates()
    assert to_usd(Decimal("7.8"), "HKD", rates) == Decimal("1")
    assert rates["HKD"] < Decimal("1")


def test_hkd_peg_is_configurable():
    rates = build_rates(hkd_peg=Decimal("7.75"))
    # 1/7.75 is not exactly representable, so compare at money precision (cents),
    # which is how every figure in the system is actually reported.
    assert q(to_usd(Decimal("7.75"), "HKD", rates)) == Decimal("1.00")
    assert q(to_usd(Decimal("775"), "HKD", rates)) == Decimal("100.00")


def test_hkd_peg_zero_rejected():
    with pytest.raises(ValueError, match="invalid HKD peg"):
        build_rates(hkd_peg=Decimal("0"))


def test_missing_rate_raises_not_silently_one():
    """A missing FX rate must never be treated as 1.0 — that silently corrupts PnL."""
    with pytest.raises(FXError, match="missing FX rate for SEK"):
        to_usd(Decimal("100"), "SEK", {})


def test_unsupported_currency_raises():
    with pytest.raises(FXError, match="unsupported currency"):
        to_usd(Decimal("100"), "EUR", {"EUR": Decimal("1")})


def test_zero_rate_raises():
    with pytest.raises(FXError, match="invalid FX rate"):
        to_usd(Decimal("100"), "SEK", {"SEK": Decimal("0")})


def test_multi_currency_portfolio_weights_sum_correctly():
    positions = {
        "AAPL": Position(ticker="AAPL", currency="USD"),
        "VOLV-B.ST": Position(ticker="VOLV-B.ST", currency="SEK"),
        "0700.HK": Position(ticker="0700.HK", currency="HKD"),
    }
    positions["AAPL"].add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100")))
    positions["VOLV-B.ST"].add_lot(
        Lot(quantity=Decimal("1000"), unit_cost=Decimal("250"), currency="SEK")
    )
    positions["0700.HK"].add_lot(
        Lot(quantity=Decimal("100"), unit_cost=Decimal("390"), currency="HKD")
    )

    # prices: AAPL 150 USD; VOLV 300 SEK; 0700 400 HKD
    prices = {
        "AAPL": Decimal("150"),
        "VOLV-B.ST": Decimal("300"),
        "0700.HK": Decimal("400"),
    }
    fx = {"SEK": Decimal("0.1")}
    view = value_positions(positions, prices, {"USD": Decimal("1000")}, fx)

    # AAPL 1500 USD; VOLV 300000*0.1 = 30000 USD; 0700 40000/7.8 = 5128.21 USD
    expected = (
        Decimal("1500") + Decimal("30000") + Decimal("40000") / Decimal("7.8")
    )
    assert view.positions_value_usd == pytest.approx(expected)
    assert view.cash_usd == Decimal("1000")
    total_weight = sum(p.weight_pct for p in view.positions)
    assert total_weight == pytest.approx(view.positions_value_usd / view.total_value_usd * 100)


def test_fx_exposure_groups_by_source_currency():
    positions = {
        "AAPL": Position(ticker="AAPL", currency="USD"),
        "VOLV-B.ST": Position(ticker="VOLV-B.ST", currency="SEK"),
    }
    positions["AAPL"].add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100")))
    positions["VOLV-B.ST"].add_lot(
        Lot(quantity=Decimal("1000"), unit_cost=Decimal("250"), currency="SEK")
    )
    view = value_positions(
        positions,
        {"AAPL": Decimal("150"), "VOLV-B.ST": Decimal("300")},
        {},
        {"SEK": Decimal("0.1")},
    )
    exposure = fx_exposure(view)
    assert exposure["USD"] == Decimal("1500")
    assert exposure["SEK"] == Decimal("30000")


def test_missing_price_raises():
    positions = {"AAPL": Position(ticker="AAPL", currency="USD")}
    positions["AAPL"].add_lot(Lot(quantity=Decimal("1"), unit_cost=Decimal("1")))
    with pytest.raises(ValueError, match="missing price for AAPL"):
        value_positions(positions, {}, {}, {})
