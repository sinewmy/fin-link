"""End-to-end workspace loading — regression tests for bugs found during Phase 0."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from finlink.domain.pnl import Position
from finlink.domain.portfolio import value_positions
from finlink.models import LedgerRow
from finlink.workspace import Config, load_workspace

LEDGER_HEADER = (
    "| date | ticker | side | quantity | price | currency | fees | reason | "
    "thesis_slug | fx_rate_usd_at_trade |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
)
LEDGER = LEDGER_HEADER + """
| 2026-01-10 | AAPL | buy | 10 | 100 | USD | - | - | - | - |
| 2026-02-01 | AAPL | sell | 4 | 120 | USD | - | - | - | - |
| 2026-03-01 | VOLV-B.ST | buy | 1000 | 250 | SEK | - | - | - | - |
| 2026-04-01 | 0700.HK | buy | 100 | 390 | HKD | - | - | - | - |
"""


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / "portfolio").mkdir()
    (tmp_path / "theses").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "portfolio" / "ledger.md").write_text(LEDGER, encoding="utf-8")
    (tmp_path / "portfolio" / "cash.md").write_text(
        "| currency | amount |\n| --- | --- |\n| USD | 10000 |\n", encoding="utf-8"
    )
    (tmp_path / "config" / "config.yaml").write_text(
        "base_currency: USD\nhkd_peg: '7.8'\nfx:\n  SEK: '0.095'\n", encoding="utf-8"
    )
    return tmp_path


def test_blank_fees_and_slugs_do_not_break_parsing(ws: Path):
    """Regression: `-` in optional columns was rejected instead of treated as absent."""
    loaded = load_workspace(ws)
    assert len(loaded.ledger) == 4
    assert all(row.fees in (None, Decimal("0")) for row in loaded.ledger)
    assert all(row.thesis_slug is None for row in loaded.ledger)


def test_ledger_replay_matches_expected_pnl(ws: Path):
    loaded = load_workspace(ws)
    positions = loaded.rebuild_positions()
    assert positions["AAPL"].quantity == Decimal("6")
    assert positions["AAPL"].realised_pnl == Decimal("80")
    assert positions["VOLV-B.ST"].currency == "SEK"
    assert positions["0700.HK"].currency == "HKD"


def test_multi_currency_valuation_end_to_end(ws: Path):
    loaded = load_workspace(ws)
    positions = loaded.rebuild_positions()
    prices = {"AAPL": Decimal("150"), "VOLV-B.ST": Decimal("300"), "0700.HK": Decimal("400")}
    view = value_positions(positions, prices, loaded.cash_by_currency(), loaded.config.fx)

    assert view.cash_usd == Decimal("10000")
    assert round(view.total_value_usd, 2) == Decimal("44528.21")
    assert round(view.unrealised_pnl_usd, 2) == Decimal("5178.21")


def test_missing_fx_rate_fails_loudly_not_silently_one(tmp_path: Path):
    """Regression: a missing SEK rate must raise, not silently treat SEK as USD."""
    (tmp_path / "portfolio").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "portfolio" / "ledger.md").write_text(
        LEDGER_HEADER + "| 2026-03-01 | VOLV-B.ST | buy | 1000 | 250 | SEK | - | - | - | - |\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "config.yaml").write_text(
        "base_currency: USD\nfx: {}\n", encoding="utf-8"
    )
    loaded = load_workspace(tmp_path)
    from finlink.domain.money import FXError

    with pytest.raises(FXError, match="missing FX rate for SEK"):
        value_positions(loaded.rebuild_positions(), {"VOLV-B.ST": Decimal("300")}, {}, {})


def test_reason_is_mandatory_not_blanked_into_none(tmp_path: Path):
    """reason is semantically required; blank must not be silently coerced to None."""
    row = LedgerRow.model_validate(
        {
            "date": "2026-01-01",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": "1",
            "price": "1",
            "currency": "USD",
            "fees": None,
            "reason": "",
            "thesis_slug": None,
            "fx_rate_usd_at_trade": None,
        }
    )
    assert row.reason == ""


def test_unsupported_currency_in_config_is_flagged():
    cfg = Config.load(Path("/nonexistent"))
    assert cfg.base_currency == "USD"


def test_position_defaults_to_usd_lot_currency():
    pos = Position(ticker="X", currency="SEK")
    from finlink.domain.pnl import Lot

    with pytest.raises(ValueError, match="currency mismatch"):
        pos.add_lot(Lot(quantity=Decimal("1"), unit_cost=Decimal("1")))


def _minimal_workspace(tmp_path: Path, positions_row: str) -> Path:
    (tmp_path / "portfolio").mkdir()
    (tmp_path / "theses").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "portfolio" / "ledger.md").write_text(
        "| date | ticker | side | quantity | price | currency | fees | reason | "
        "thesis_slug | fx_rate_usd_at_trade |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n",
        encoding="utf-8",
    )
    (tmp_path / "portfolio" / "cash.md").write_text(
        "| currency | amount |\n| --- | --- |\n| USD | 0 |\n", encoding="utf-8"
    )
    (tmp_path / "config" / "config.yaml").write_text("base_currency: USD\n", encoding="utf-8")
    (tmp_path / "portfolio" / "positions.md").write_text(
        "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n" + positions_row,
        encoding="utf-8",
    )
    return tmp_path


def test_placeholder_thesis_slug_is_not_a_dangling_link(tmp_path: Path):
    """Regression: `-` in thesis_slug was reported as a dangling thesis link."""
    from finlink.doctor import run

    ws = _minimal_workspace(tmp_path, "| AAPL | 10 | 100 | USD | 2026-01-01 | - | - |\n")
    assert run(ws) is True


def test_real_dangling_thesis_link_is_caught(tmp_path: Path):
    from finlink.doctor import run

    ws = _minimal_workspace(
        tmp_path, "| AAPL | 10 | 100 | USD | 2026-01-01 | does-not-exist | - |\n"
    )
    assert run(ws) is False
