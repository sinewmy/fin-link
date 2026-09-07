"""Periodic portfolio snapshots for historical drift (Phase 5).

Each run appends one row to `data/snapshots/<metric>.csv`. The cache is disposable
and idempotent: re-running on the same day overwrites that day's row rather than
duplicating it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from finlink.domain.money import q

ZERO = Decimal("0")
PCT = Decimal("0.0001")


@dataclass(frozen=True)
class Snapshot:
    day: date
    total_usd: Decimal
    positions_usd: Decimal
    cash_usd: Decimal
    cash_pct: Decimal
    weights: dict[str, Decimal]  # ticker -> percent
    sector_weights: dict[str, Decimal]
    country_weights: dict[str, Decimal]
    currency_weights: dict[str, Decimal]
    hhi: Decimal
    volatility_pct: Decimal | None = None

    def weights_json(self) -> str:
        import json

        return json.dumps(
            {
                "position": {k: str(v) for k, v in sorted(self.weights.items())},
                "sector": {k: str(v) for k, v in sorted(self.sector_weights.items())},
                "country": {k: str(v) for k, v in sorted(self.country_weights.items())},
                "currency": {k: str(v) for k, v in sorted(self.currency_weights.items())},
            },
            ensure_ascii=False,
        )


FIELDNAMES = [
    "day",
    "total_usd",
    "positions_usd",
    "cash_usd",
    "cash_pct",
    "hhi",
    "volatility_pct",
    "weights_json",
]


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "data" / "snapshots" / "portfolio.csv"

    def append(self, snap: Snapshot) -> bool:
        """Idempotent by day. Returns True if a row was added."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.load_raw()
        day = snap.day.isoformat()
        rows = [r for r in rows if r["day"] != day]
        rows.append(
            {
                "day": day,
                "total_usd": str(q(snap.total_usd)),
                "positions_usd": str(q(snap.positions_usd)),
                "cash_usd": str(q(snap.cash_usd)),
                "cash_pct": str(q(snap.cash_pct, PCT)),
                "hhi": str(q(snap.hhi, PCT)),
                "volatility_pct": (
                    "" if snap.volatility_pct is None else str(q(snap.volatility_pct, PCT))
                ),
                "weights_json": (snap.weights_json() or "").replace("|", "\\|"),
            }
        )
        rows.sort(key=lambda r: r["day"])
        with self.path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(rows)
        return True

    def load_raw(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def latest(self) -> Snapshot | None:
        snaps = self.load()
        return snaps[-1] if snaps else None

    def load(self) -> list[Snapshot]:
        import json

        out: list[Snapshot] = []
        for r in self.load_raw():
            try:
                raw = json.loads((r.get("weights_json") or "{}").replace("\\|", "|"))
            except json.JSONDecodeError:
                raw = {}
            out.append(
                Snapshot(
                    day=date.fromisoformat(r["day"]),
                    total_usd=Decimal(r["total_usd"] or "0"),
                    positions_usd=Decimal(r["positions_usd"] or "0"),
                    cash_usd=Decimal(r["cash_usd"] or "0"),
                    cash_pct=Decimal(r["cash_pct"] or "0"),
                    weights={k: Decimal(v) for k, v in (raw.get("position") or {}).items()},
                    sector_weights={k: Decimal(v) for k, v in (raw.get("sector") or {}).items()},
                    country_weights={k: Decimal(v) for k, v in (raw.get("country") or {}).items()},
                    currency_weights={
                        k: Decimal(v) for k, v in (raw.get("currency") or {}).items()
                    },
                    hhi=Decimal(r["hhi"] or "0"),
                    volatility_pct=(
                        Decimal(r["volatility_pct"]) if r.get("volatility_pct") else None
                    ),
                )
            )
        out.sort(key=lambda s: s.day)
        return out


def series(snaps: list[Snapshot], key: str) -> list[tuple[date, Decimal]]:
    """Extract a time series for charting: hhi, cash_pct, total_usd, weight:<TICKER>."""
    out: list[tuple[date, Decimal]] = []
    for s in snaps:
        if key == "hhi":
            out.append((s.day, s.hhi))
        elif key == "cash_pct":
            out.append((s.day, s.cash_pct))
        elif key == "total_usd":
            out.append((s.day, s.total_usd))
        elif key.startswith("weight:"):
            ticker = key.split(":", 1)[1].upper()
            out.append((s.day, s.weights.get(ticker, ZERO)))
    return out
