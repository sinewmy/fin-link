"""Portfolio context for a validation (TECHNICAL_DESIGN 4.4).

Computed in code and handed to the model as read-only numbers. The model may
interpret them but never restate, recompute or contradict them: every figure the
context contains is listed in `allowed_numbers` so a `position_note` can be checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finlink.domain.money import q
from finlink.domain.portfolio import PortfolioView
from finlink.domain.risk import Alert

ZERO = Decimal("0")
PCT = Decimal("0.01")


@dataclass(frozen=True)
class PositionContext:
    ticker: str
    weight_pct: Decimal
    sector: str
    sector_weight_pct: Decimal
    country: str
    country_weight_pct: Decimal
    cash_pct: Decimal
    holding_count: int
    breaches: tuple[Alert, ...]

    @property
    def breached(self) -> bool:
        return bool(self.breaches)


@dataclass(frozen=True)
class PortfolioContext:
    position: PositionContext | None
    holdings: tuple[PositionContext, ...]
    cash_pct: Decimal
    total_value_usd: Decimal
    unpriced: bool = False
    note: str = ""

    @property
    def allowed_numbers(self) -> set[str]:
        """Every numeric token the model is permitted to mention.

        Several renderings of each figure are listed ('22.3' and '22.30') because the
        model writes prose, not Decimals.
        """
        out: set[str] = set()
        if self.position is None:
            return out
        p = self.position
        for value in (
            p.weight_pct,
            p.sector_weight_pct,
            p.country_weight_pct,
            self.cash_pct,
        ):
            out |= _norm(value)
        out.add(str(p.holding_count))
        out |= _norm(self.total_value_usd)
        for a in p.breaches:
            out |= _norm(a.limit_pct)
            out |= _norm(a.value_pct)
        for h in self.holdings:
            out |= _norm(h.weight_pct)
        return {x for x in out if x}

    def render(self) -> str:
        """The exact text written into '### Portfolio context' — produced by code."""
        if self.position is None:
            reason = f" ({self.note})" if self.note else ""
            return f"no position held{reason}"
        p = self.position
        parts = [f"weight {q(p.weight_pct, PCT)}%"]
        if p.sector:
            parts.append(f"sector({p.sector}) {q(p.sector_weight_pct, PCT)}%")
        if p.country:
            parts.append(f"country({p.country}) {q(p.country_weight_pct, PCT)}%")
        parts.append(f"holdings {p.holding_count}")
        parts.append(f"cash {q(self.cash_pct, PCT)}%")
        if p.breaches:
            for a in p.breaches:
                parts.append(
                    f"LIMIT {a.rule_id} {a.scope} {q(a.value_pct, PCT)}% vs "
                    f"{q(a.limit_pct, PCT)}% BREACHED"
                )
        else:
            parts.append("no limits breached")
        return " | ".join(parts)


def _norm(value: Decimal) -> set[str]:
    """Normalised numeric forms, so '22.3', '22.30' and '22' all count as known."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    forms = {text or "0"}
    forms.add(f"{q(value, PCT):f}".rstrip("0").rstrip("."))
    forms.add(f"{q(value, Decimal('0.0001')):f}".rstrip("0").rstrip("."))
    return {f or "0" for f in forms}


def build_context(
    view: PortfolioView,
    ticker: str,
    *,
    sectors: dict[str, str] | None = None,
    countries: dict[str, str] | None = None,
    alerts: list[Alert] | None = None,
) -> PortfolioContext:
    sectors = sectors or {}
    countries = countries or {}
    alerts = alerts or []
    sw = sector_weights(view, sectors)
    cw = country_weights(view, countries)

    holdings: list[PositionContext] = []
    for p in view.positions:
        holdings.append(
            PositionContext(
                ticker=p.ticker,
                weight_pct=p.weight_pct,
                sector=sectors.get(p.ticker.upper(), ""),
                sector_weight_pct=sw.get(sectors.get(p.ticker.upper(), ""), ZERO),
                country=countries.get(p.ticker.upper(), ""),
                country_weight_pct=cw.get(countries.get(p.ticker.upper(), ""), ZERO),
                cash_pct=view.cash_pct,
                holding_count=len(view.positions),
                breaches=tuple(
                    a
                    for a in alerts
                    if a.scope.upper() == p.ticker.upper()
                    or a.scope == sectors.get(p.ticker.upper(), "")
                ),
            )
        )
    match = next((h for h in holdings if h.ticker.upper() == ticker.upper()), None)
    if match is None:
        # Not held (or not priced) — still report the portfolio totals. A validation
        # must never quietly invent a zero weight for a name we cannot value.
        return PortfolioContext(
            position=None,
            holdings=tuple(holdings),
            cash_pct=view.cash_pct,
            total_value_usd=view.total_value_usd,
            unpriced=True,
            note="ticker not in priced holdings",
        )
    return PortfolioContext(
        position=match,
        holdings=tuple(holdings),
        cash_pct=view.cash_pct,
        total_value_usd=view.total_value_usd,
    )


def sector_weights(view: PortfolioView, sectors: dict[str, str]) -> dict[str, Decimal]:
    from finlink.domain.risk import sector_weights as _sw

    return _sw(view, sectors)


def country_weights(view: PortfolioView, countries: dict[str, str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for p in view.positions:
        key = countries.get(p.ticker.upper(), "Unknown")
        out[key] = out.get(key, ZERO) + p.weight_pct
    return out


@dataclass(frozen=True)
class NoContext:
    """Used when the portfolio cannot be valued at all (no prices)."""

    reason: str = "portfolio could not be valued (missing prices)"
    unpriced: bool = True
    holdings: tuple[PositionContext, ...] = ()
    cash_pct: Decimal = ZERO
    total_value_usd: Decimal = ZERO
    position: None = None

    @property
    def allowed_numbers(self) -> set[str]:
        return set()

    def render(self) -> str:
        return f"unavailable — {self.reason}"
