"""Locate and load the fin-link workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from finlink.domain.pnl import Lot, Position
from finlink.io.markdown import MarkdownError, parse_table, read_document
from finlink.models import CashEntry, LedgerRow, PositionRow, ThesisFrontmatter

MARKER = ".finlink"


def find_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / MARKER).exists() or (candidate / "config" / "config.yaml").exists():
            return candidate
        if (candidate / ".git").exists() and (candidate / "portfolio").is_dir():
            return candidate
    raise RuntimeError(
        f"not a fin-link workspace (no {MARKER} or config/config.yaml). "
        "Run `finlink init <dir>` first."
    )


@dataclass
class Config:
    base_currency: str = "USD"
    hkd_peg: Decimal = Decimal("7.8")
    fx: dict[str, Decimal] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    risk_rules: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> Config:
        path = root / "config" / "config.yaml"
        if not path.exists():
            return cls(fx={}, models={}, risk_rules=[])
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            base_currency=str(raw.get("base_currency", "USD")).upper(),
            hkd_peg=Decimal(str(raw.get("hkd_peg", "7.8"))),
            fx={k.upper(): Decimal(str(v)) for k, v in (raw.get("fx") or {}).items()},
            models=dict(raw.get("models") or {}),
            risk_rules=list(raw.get("risk_rules") or []),
        )


@dataclass
class Workspace:
    root: Path
    config: Config

    @property
    def portfolio_dir(self) -> Path:
        return self.root / "portfolio"

    @property
    def theses_dir(self) -> Path:
        return self.root / "theses"

    def load(self) -> LoadedWorkspace:
        return load_workspace(self.root)


@dataclass
class LoadedWorkspace:
    root: Path
    config: Config
    positions: list[PositionRow]
    ledger: list[LedgerRow]
    cash: list[CashEntry]
    theses: list[tuple[Path, ThesisFrontmatter]]

    def cash_by_currency(self) -> dict[str, Decimal]:
        return {c.currency: c.amount for c in self.cash}

    def rebuild_positions(self) -> dict[str, Position]:
        """Replay the ledger FIFO into Position objects. Ledger is the source of truth."""
        positions: dict[str, Position] = {}
        for row in sorted(self.ledger, key=lambda r: (r.date, r.ticker)):
            pos = positions.get(row.ticker)
            if row.side.value == "buy":
                if pos is None:
                    pos = Position(ticker=row.ticker, currency=row.currency)
                    positions[row.ticker] = pos
                pos.add_lot(
                    Lot(
                        quantity=row.quantity,
                        unit_cost=row.price,
                        currency=row.currency,
                        opened_at=row.date.isoformat(),
                        fees=row.fees or Decimal("0"),
                    )
                )
            else:
                if pos is None:
                    raise MarkdownError(
                        f"ledger: sell of {row.ticker} on {row.date} before any buy"
                    )
                pos.reduce(row.quantity, row.price, row.fees or Decimal("0"))
        return positions


def load_workspace(root: Path) -> LoadedWorkspace:
    cfg = Config.load(root)
    pdir = root / "portfolio"

    positions: list[PositionRow] = []
    pfile = pdir / "positions.md"
    if pfile.exists():
        for row in parse_table(pfile.read_text(encoding="utf-8")):
            clean_row = {k: v for k, v in row.items() if v is not None and v not in ("", "-")}
            positions.append(PositionRow.model_validate(clean_row))

    ledger: list[LedgerRow] = []
    lfile = pdir / "ledger.md"
    if lfile.exists():
        # Only genuinely optional fields may become None; blank mandatory strings
        # (e.g. reason) stay blank so the model reports a real validation error.
        optional_fields = {"thesis_slug", "fx_rate_usd_at_trade", "fees"}
        for row in parse_table(lfile.read_text(encoding="utf-8")):
            blanks_as_none: dict[str, str | None] = {
                k: (None if v in ("", "-") and k in optional_fields else v)
                for k, v in row.items()
                if v is not None
            }
            ledger.append(LedgerRow.model_validate(blanks_as_none))

    cash: list[CashEntry] = []
    cfile = pdir / "cash.md"
    if cfile.exists():
        for row in parse_table(cfile.read_text(encoding="utf-8")):
            clean_row = {k: v for k, v in row.items() if v is not None and v not in ("", "-")}
            cash.append(CashEntry.model_validate(clean_row))

    theses: list[tuple[Path, ThesisFrontmatter]] = []
    tdir = root / "theses"
    if tdir.is_dir():
        for f in sorted(tdir.glob("*.md")):
            doc = read_document(f)
            theses.append((f, ThesisFrontmatter.model_validate(doc.frontmatter)))

    return LoadedWorkspace(
        root=root, config=cfg, positions=positions, ledger=ledger, cash=cash, theses=theses
    )
