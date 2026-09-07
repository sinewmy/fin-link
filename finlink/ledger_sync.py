"""Single write-owner for keeping ledger, positions and cash in sync.

Design (Model Y — three registries, one consistency contract):

  * `ledger.md` is the EVENT LOG — append-only history of what happened.
  * `positions.md` is the CURRENT-HOLDINGS registry — "what do I own now".
  * `cash.md` is the CURRENT-CASH registry — "how much do I have".

The ledger is the source of truth: positions and cash are derived FROM it by
`record-trade` and `seed-ledger`, and `doctor` later verifies they agree.

This module is the ONLY place that rewrites `positions.md`/`cash.md` from the
ledger. Everything here is pure logic over already-validated ledger rows.
"""

from __future__ import annotations

from contextlib import suppress
from decimal import Decimal
from pathlib import Path

from finlink.io.markdown import render_table, write_atomic
from finlink.models import LedgerRow
from finlink.workspace import LoadedWorkspace

HEADERS_POSITIONS = [
    "ticker",
    "quantity",
    "avg_cost",
    "currency",
    "opened_at",
    "thesis_slug",
    "notes",
]
HEADERS_LEDGER = [
    "date",
    "ticker",
    "side",
    "quantity",
    "price",
    "currency",
    "fees",
    "reason",
    "thesis_slug",
    "fx_rate_usd_at_trade",
]
HEADERS_CASH = ["currency", "amount"]

ZERO = Decimal("0")


# ---------- derived views over the ledger ----------


def positions_from_ledger(rows: list[LedgerRow]) -> list[dict[str, str]]:
    """Current holdings derived from ledger rows (FIFO lots via domain/pnl).

    Returns rows ready for render_table: same shape as positions.md today, with
    avg_cost in the position's source currency and opened_at = date of first buy.
    """
    from finlink.domain.pnl import Lot, Position

    positions: dict[str, Position] = {}
    opened: dict[str, str] = {}
    thesis: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: (r.date, r.ticker)):
        pos = positions.get(row.ticker)
        if pos is None:
            pos = Position(ticker=row.ticker, currency=row.currency)
            positions[row.ticker] = pos
            opened[row.ticker] = row.date.isoformat()
        elif pos.currency != row.currency:
            raise ValueError(
                f"ledger currency mismatch for {row.ticker}: {pos.currency} vs {row.currency}"
            )
        if row.side.value == "buy":
            pos.add_lot(
                Lot(
                    quantity=row.quantity,
                    unit_cost=row.price,
                    currency=row.currency,
                    opened_at=row.date.isoformat(),
                    fees=row.fees or ZERO,
                )
            )
        else:
            pos.reduce(row.quantity, row.price, row.fees or ZERO)
        if row.thesis_slug and row.side.value == "buy":
            thesis[row.ticker] = row.thesis_slug

    out: list[dict[str, str]] = []
    for ticker in sorted(positions):
        pos = positions[ticker]
        qty = pos.quantity
        if qty == 0:
            continue
        avg = pos.avg_cost
        out.append(
            {
                "ticker": ticker,
                "quantity": _fmt(qty),
                "avg_cost": _fmt(avg),
                "currency": pos.currency,
                "opened_at": opened[ticker],
                "thesis_slug": thesis.get(ticker, ""),
                "notes": "",
            }
        )
    return out


def cash_delta(row: LedgerRow) -> Decimal:
    """Signed cash effect of ONE ledger row, in the trade's source currency.

    Buy debits `quantity*price + fees`; sell credits `quantity*price - fees`.
    """
    delta = row.quantity * row.price
    if row.side.value == "buy":
        return -(delta + (row.fees or ZERO))
    return delta - (row.fees or ZERO)


def apply_cash_delta(
    cash: list[dict[str, str]], delta: Decimal, currency: str
) -> list[dict[str, str]]:
    """Return cash balances updated by `delta` in `currency`.

    cash is the CURRENT cash registry (not derived from history); we adjust only
    the one currency affected by the trade. Other currencies pass through.
    """
    balances: dict[str, Decimal] = {}
    for c in cash:
        balances[c["currency"]] = Decimal(c["amount"])
    balances[currency] = balances.get(currency, ZERO) + delta
    return [
        {"currency": ccy, "amount": _fmt(amount)} for ccy, amount in sorted(balances.items())
    ]


# ---------- file writes (the only mutators) ----------


def write_ledger_rows(root: Path, rows: list[dict[str, str]]) -> None:
    """(Re)write ledger.md from full rows (used by seed-ledger)."""
    write_atomic(root / "portfolio" / "ledger.md", _render("Ledger", HEADERS_LEDGER, rows))


def write_positions(root: Path, rows: list[dict[str, str]]) -> None:
    write_atomic(root / "portfolio" / "positions.md", _render("Positions", HEADERS_POSITIONS, rows))


def write_cash(root: Path, rows: list[dict[str, str]]) -> None:
    write_atomic(root / "portfolio" / "cash.md", _render("Cash", HEADERS_CASH, rows))


def _render(title: str, headers: list[str], rows: list[dict[str, str]]) -> str:
    return f"# {title}\n\n" + render_table(rows, headers) + "\n"


def _fmt(value: Decimal) -> str:
    """Keep the same decimal display as the rest of the CLI (no scientific, no trailing junk)."""
    if value == value.to_integral():
        return format(value, "f")
    return format(value.normalize(), "f")


# ---------- validation / consistency ----------


def positions_match(root: Path, ws: LoadedWorkspace) -> bool:
    """True when positions.md (parsed) equals the ledger-derived view (ignoring notes).

    Used by `doctor` to catch drift without touching files.
    """
    from finlink.io.markdown import parse_table

    current = parse_table((root / "portfolio" / "positions.md").read_text(encoding="utf-8"))
    expected = positions_from_ledger(ws.ledger)
    left = [_norm(r) for r in current]
    right = [_norm(r) for r in expected]
    return left == right


def _norm(row: dict[str, str]) -> dict[str, str]:
    """Normalise a positions row for comparison:
    - ignore `notes` entirely (hand-authored, not derived)
    - compare numeric cells by Decimal value so '100' == '100.0'
    """
    out: dict[str, str] = {}
    for k in HEADERS_POSITIONS:
        if k == "notes" or k == "opened_at" or k == "thesis_slug":
            out[k] = row.get(k, "")
            continue
        v = row.get(k, "")
        with suppress(ValueError, ArithmeticError):
            v = format(Decimal(v), "f")
        out[k] = v
    out.pop("notes", None)
    return out
