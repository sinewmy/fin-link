"""Markdown rendering for CLI output. No numbers are invented here."""

from __future__ import annotations

from decimal import Decimal

from finlink.domain.money import q
from finlink.domain.pnl import Position
from finlink.domain.portfolio import value_positions
from finlink.workspace import LoadedWorkspace


def render_portfolio(ws: LoadedWorkspace, prices: dict[str, Decimal] | None = None) -> str:
    from finlink.io.markdown import render_table

    prices = prices or {}
    positions = ws.rebuild_positions() if ws.ledger else {}
    if not positions:
        for row in ws.positions:
            pos = positions.setdefault(
                row.ticker, Position(ticker=row.ticker, currency=row.currency)
            )
            from finlink.domain.pnl import Lot

            pos.add_lot(
                Lot(
                    quantity=row.quantity,
                    unit_cost=row.avg_cost,
                    currency=row.currency,
                    opened_at=row.opened_at.isoformat() if row.opened_at else "",
                )
            )

    missing = [t for t in positions if t not in prices]
    if missing:
        return (
            "Cannot value portfolio: no prices for " + ", ".join(sorted(missing)) + "\n"
            "Run `finlink ingest` (Phase 2) or pass prices explicitly."
        )

    view = value_positions(positions, prices, ws.cash_by_currency(), ws.config.fx)
    rows = [
        {
            "ticker": p.ticker,
            "qty": str(q(p.quantity, Decimal("0.000001"))),
            "avg_cost": f"{q(p.avg_cost)} {p.currency}",
            "price": f"{q(p.price)} {p.currency}",
            "mkt_usd": str(q(p.market_value_usd)),
            "pnl_usd": str(q(p.unrealised_pnl_usd)),
            "weight": f"{q(p.weight_pct, Decimal('0.01'))}%",
        }
        for p in sorted(view.positions, key=lambda x: x.weight_pct, reverse=True)
    ]
    out = [
        "# Portfolio\n",
        render_table(
            rows,
            ["ticker", "qty", "avg_cost", "price", "mkt_usd", "pnl_usd", "weight"],
        ),
        "",
        f"Positions (USD): {q(view.positions_value_usd)}",
        f"Cash (USD):      {q(view.cash_usd)}",
        f"Total (USD):     {q(view.total_value_usd)}",
        f"Unrealised PnL:  {q(view.unrealised_pnl_usd)}",
        f"Cash %:          {q(view.cash_pct, Decimal('0.01'))}%",
        "",
        "_Base currency: USD. HKD pegged; SEK converted at configured rate._",
    ]
    return "\n".join(out)
