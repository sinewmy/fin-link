"""Tests for ledger-as-truth: positions/cash derived from ledger, seed-ledger, collisions."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from finlink.cli import main
from finlink.ledger_sync import (
    apply_cash_delta,
    cash_delta,
    positions_from_ledger,
    positions_match,
)
from finlink.workspace import load_workspace

LEDGER_HEADER = (
    "| date | ticker | side | quantity | price | currency | fees | reason | "
    "thesis_slug | fx_rate_usd_at_trade |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
)
POS_HEADER = (
    "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
    "| --- | --- | --- | --- | --- | --- | --- |\n"
)
CASH_HEADER = "| currency | amount |\n| --- | --- |\n"


def _workspace(tmp_path: Path, *, cash: str = "| USD | 10000 |\n| HKD | 200000 |\n") -> Path:
    (tmp_path / "portfolio").mkdir()
    (tmp_path / "theses").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "portfolio" / "ledger.md").write_text(LEDGER_HEADER, encoding="utf-8")
    (tmp_path / "portfolio" / "cash.md").write_text(CASH_HEADER + cash, encoding="utf-8")
    (tmp_path / "config" / "config.yaml").write_text(
        "base_currency: USD\nhkd_peg: '7.8'\nfx:\n  SEK: '0.095'\n", encoding="utf-8"
    )
    (tmp_path / ".finlink").write_text("", encoding="utf-8")
    return tmp_path


def _buy(ticker: str = "AAPL", qty: str = "10", price: str = "100", **kw) -> str:
    row = {
        "date": "2026-01-01",
        "ticker": ticker,
        "side": "buy",
        "quantity": qty,
        "price": price,
        "currency": "USD",
        "fees": "-",
        "reason": "reason",
        "thesis_slug": "-",
        "fx_rate_usd_at_trade": "-",
    }
    row.update({k: v for k, v in kw.items() if v is not None})
    return "| " + " | ".join(str(row[k]) for k in [
        "date", "ticker", "side", "quantity", "price", "currency",
        "fees", "reason", "thesis_slug", "fx_rate_usd_at_trade",
    ]) + " |"


# ---------- pure derivation ----------


def test_positions_derived_from_buy(tmp_path: Path):
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(
        LEDGER_HEADER + _buy() + "\n", encoding="utf-8"
    )
    loaded = load_workspace(ws)
    rows = positions_from_ledger(loaded.ledger)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert Decimal(rows[0]["quantity"]) == Decimal("10")
    assert Decimal(rows[0]["avg_cost"]) == Decimal("100")


def test_positions_reflect_buy_and_sell(tmp_path: Path):
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(
        LEDGER_HEADER
        + _buy() + "\n"
        + _buy(side="sell", qty="4", price="120", reason="") + "\n",
        encoding="utf-8",
    )
    loaded = load_workspace(ws)
    rows = positions_from_ledger(loaded.ledger)
    assert len(rows) == 1
    assert Decimal(rows[0]["quantity"]) == Decimal("6")
    assert Decimal(rows[0]["avg_cost"]) == Decimal("100")


def test_cash_delta_matches_doc():
    """cash_delta applies one trade incrementally, per currency."""
    from finlink.models import LedgerRow

    def r(side: str, qty: str, price: str, fees: Decimal) -> LedgerRow:
        return LedgerRow.model_validate(
            {
                "date": "2026-01-01",
                "ticker": "AAPL",
                "side": side,
                "quantity": qty,
                "price": price,
                "currency": "USD",
                "fees": fees,
            }
        )

    buy = r("buy", "10", "100", Decimal("5"))
    sell = r("sell", "4", "120", Decimal("5"))
    assert cash_delta(buy) == -(Decimal("10") * Decimal("100") + Decimal("5"))
    assert cash_delta(sell) == Decimal("4") * Decimal("120") - Decimal("5")


def test_apply_cash_delta_preserves_other_currencies():
    cash = apply_cash_delta([{"currency": "HKD", "amount": "200000"}], Decimal("-150000"), "HKD")
    assert {c["currency"]: c["amount"] for c in cash} == {"HKD": "50000"}
    # A currency absent from the registry is added with the delta applied.
    cash2 = apply_cash_delta([{"currency": "HKD", "amount": "200000"}], Decimal("-600"), "USD")
    assert {c["currency"]: c["amount"] for c in cash2} == {"HKD": "200000", "USD": "-600"}


# ---------- positions_match (doctor-style consistency) ----------


def test_positions_match_after_exact_rerender(tmp_path: Path):
    from finlink.ledger_sync import write_positions

    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(
        LEDGER_HEADER + _buy() + "\n", encoding="utf-8"
    )
    loaded = load_workspace(ws)
    write_positions(ws, positions_from_ledger(loaded.ledger))
    assert positions_match(ws, load_workspace(ws))


def test_positions_match_detects_drift(tmp_path: Path):
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(
        LEDGER_HEADER + _buy() + "\n", encoding="utf-8"
    )
    (ws / "portfolio" / "positions.md").write_text(
        POS_HEADER + "| AAPL | 99 | 100 | USD | 2026-01-01 | - | - |\n", encoding="utf-8"
    )
    assert not positions_match(ws, load_workspace(ws))


# ---------- CLI: seed-ledger ----------


def test_seed_ledger_roundtrip(tmp_path: Path, monkeypatch):
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "positions.md").write_text(
        POS_HEADER
        + "| AAPL | 10 | 100 | USD | 2026-01-01 | ai-capex | bought low |\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(main, ["seed-ledger", "--no-commit"])
    assert result.exit_code == 0, result.output

    loaded = load_workspace(ws)
    assert len(loaded.ledger) == 1
    row = loaded.ledger[0]
    assert row.side.value == "buy"
    assert row.ticker == "AAPL"
    assert row.quantity == Decimal("10")
    assert row.price == Decimal("100")
    assert row.thesis_slug == "ai-capex"
    # positions.md unchanged by seed; must still match ledger.
    assert positions_match(ws, load_workspace(ws))


def test_seed_ledger_refuses_when_ledger_has_rows(tmp_path: Path, monkeypatch):
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(
        LEDGER_HEADER + _buy() + "\n", encoding="utf-8"
    )
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(main, ["seed-ledger", "--no-commit"])
    assert result.exit_code != 0
    assert "already has rows" in result.output


def test_seed_ledger_no_positions_fails(tmp_path: Path, monkeypatch):
    ws = _workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(main, ["seed-ledger", "--no-commit"])
    assert result.exit_code != 0
    assert "no positions" in result.output


# ---------- CLI: record-trade sync ----------


def test_record_trade_updates_positions_and_cash(tmp_path: Path, monkeypatch):
    """A buy via record-trade appends to ledger AND re-syncs positions.md + cash.md."""
    ws = _workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(
        main,
        ["record-trade", "--ticker", "NVDA", "--side", "buy", "--quantity", "20",
         "--price", "175", "--reason", "ai datacenter capex", "--driver", "echo",
         "--no-commit"],
    )
    assert result.exit_code == 0, result.output

    loaded = load_workspace(ws)
    assert len(loaded.ledger) == 1
    # positions.md reflects the buy.
    assert positions_match(ws, loaded)
    # cash.md debited in USD.
    cash = loaded.cash_by_currency()
    assert cash["USD"] == Decimal("10000") - Decimal("20") * Decimal("175")
    # positions.md holds NVDA 20 @175.
    from finlink.io.markdown import parse_table

    pos_rows = parse_table((ws / "portfolio" / "positions.md").read_text(encoding="utf-8"))
    assert any(r["ticker"] == "NVDA" and Decimal(r["quantity"]) == Decimal("20") for r in pos_rows)


def test_record_trade_sell_does_not_create_thesis(tmp_path: Path, monkeypatch):
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(LEDGER_HEADER + _buy() + "\n", encoding="utf-8")
    monkeypatch.chdir(ws)
    # Re-sync positions to ledger first so the sell works.
    from finlink.ledger_sync import write_positions
    from finlink.workspace import load_workspace as lw

    loaded = lw(ws)
    write_positions(ws, positions_from_ledger(loaded.ledger))
    result = CliRunner().invoke(
        main,
        ["record-trade", "--ticker", "AAPL", "--side", "sell", "--quantity", "4",
         "--price", "120", "--reason", "", "--driver", "echo", "--no-commit"],
    )
    assert result.exit_code == 0, result.output
    assert "no new thesis for sells" in result.output
    # thesis dir empty
    assert not list((ws / "theses").iterdir())


def test_record_trade_sell_requires_no_driver(tmp_path: Path, monkeypatch):
    """Sell must not require an API key — it never calls the LLM."""
    ws = _workspace(tmp_path)
    (ws / "portfolio" / "ledger.md").write_text(LEDGER_HEADER + _buy() + "\n", encoding="utf-8")
    from finlink.ledger_sync import write_positions
    from finlink.workspace import load_workspace as lw

    write_positions(ws, positions_from_ledger(lw(ws).ledger))
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(
        main,
        ["record-trade", "--ticker", "AAPL", "--side", "sell", "--quantity", "4",
         "--price", "120", "--reason", "", "--no-commit"],
    )
    assert result.exit_code == 0, result.output
