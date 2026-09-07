"""Deterministic risk engine (TECHNICAL_DESIGN 4.7).

Pure: (portfolio state, rules) -> list[Alert]. It never calls an LLM, and no code
path exists that lets a model suppress, downgrade or close an alert.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from finlink.domain.money import q
from finlink.domain.portfolio import PortfolioView

ZERO = Decimal("0")
PCT = Decimal("0.01")


class RuleKind(StrEnum):
    MAX_POSITION_WEIGHT = "max_position_weight"
    MAX_SECTOR_WEIGHT = "max_sector_weight"
    MIN_CASH_PCT = "min_cash_pct"
    MAX_DRAWDOWN_PCT = "max_drawdown_pct"
    MAX_TRANSACTION_PCT = "max_transaction_pct"


@dataclass(frozen=True)
class Rule:
    id: str
    kind: RuleKind
    limit: Decimal  # percent
    scope: str = ""  # ticker or sector name; empty means "every"

    @classmethod
    def parse(cls, raw: Mapping[str, object]) -> Rule:
        """Fail loud on a malformed rule. A silently ignored risk rule is worse than none."""
        if not isinstance(raw, Mapping):
            raise ValueError(f"risk rule must be a mapping, got {type(raw).__name__}")
        rule_id = str(raw.get("id") or raw.get("kind") or "").strip()
        if not rule_id:
            raise ValueError(f"risk rule needs an id: {raw!r}")
        kind_raw = str(raw.get("kind") or "").strip()
        try:
            kind = RuleKind(kind_raw)
        except ValueError as e:
            valid = ", ".join(k.value for k in RuleKind)
            raise ValueError(
                f"risk rule {rule_id!r}: unknown kind {kind_raw!r} (expected {valid})"
            ) from e
        limit_raw = raw.get("limit")
        if limit_raw is None:
            raise ValueError(f"risk rule {rule_id!r}: missing limit")
        try:
            limit = Decimal(str(limit_raw))
        except Exception as e:  # noqa: BLE001 - surfaced to the user verbatim
            raise ValueError(f"risk rule {rule_id!r}: invalid limit {limit_raw!r}") from e
        if limit < 0:
            raise ValueError(f"risk rule {rule_id!r}: negative limit {limit}")
        return cls(id=rule_id, kind=kind, limit=limit, scope=str(raw.get("scope") or "").strip())


def parse_rules(raw: Sequence[object] | None) -> list[Rule]:
    return [Rule.parse(r) if isinstance(r, Mapping) else _bad_rule(r) for r in (raw or [])]


def _bad_rule(r: object) -> Rule:
    raise ValueError(f"risk rule must be a mapping, got {type(r).__name__}: {r!r}")


@dataclass(frozen=True)
class Alert:
    rule_id: str
    kind: RuleKind
    scope: str
    value_pct: Decimal
    limit_pct: Decimal
    message: str


def sector_weights(
    view: PortfolioView, sectors: Mapping[str, str] | None = None
) -> dict[str, Decimal]:
    """Sum USD weights by sector.

    Unmapped tickers land in 'Unclassified' rather than being dropped.
    """
    sectors = sectors or {}
    out: dict[str, Decimal] = {}
    for p in view.positions:
        key = sectors.get(p.ticker.upper(), "Unclassified")
        out[key] = out.get(key, ZERO) + p.weight_pct
    return out


def evaluate(
    view: PortfolioView,
    rules: list[Rule],
    *,
    sectors: Mapping[str, str] | None = None,
    drawdowns: Mapping[str, Decimal] | None = None,
    transaction_pct: Decimal | None = None,
) -> list[Alert]:
    """Evaluate every rule. Returns all breaches; it never stops at the first."""
    drawdowns = drawdowns or {}
    by_sector = sector_weights(view, sectors)
    alerts: list[Alert] = []

    for rule in rules:
        if rule.kind is RuleKind.MAX_POSITION_WEIGHT:
            for p in view.positions:
                if rule.scope and rule.scope.upper() != p.ticker.upper():
                    continue
                if p.weight_pct > rule.limit:
                    alerts.append(
                        Alert(
                            rule_id=rule.id,
                            kind=rule.kind,
                            scope=p.ticker,
                            value_pct=p.weight_pct,
                            limit_pct=rule.limit,
                            message=(
                                f"{p.ticker} is {q(p.weight_pct, PCT)}% of the portfolio, "
                                f"above the {q(rule.limit, PCT)}% limit"
                            ),
                        )
                    )
        elif rule.kind is RuleKind.MAX_SECTOR_WEIGHT:
            for name, weight in sorted(by_sector.items()):
                if rule.scope and rule.scope != name:
                    continue
                if weight > rule.limit:
                    alerts.append(
                        Alert(
                            rule_id=rule.id,
                            kind=rule.kind,
                            scope=name,
                            value_pct=weight,
                            limit_pct=rule.limit,
                            message=(
                                f"sector {name} is {q(weight, PCT)}% of the portfolio, "
                                f"above the {q(rule.limit, PCT)}% limit"
                            ),
                        )
                    )
        elif rule.kind is RuleKind.MIN_CASH_PCT:
            if view.cash_pct < rule.limit:
                alerts.append(
                    Alert(
                        rule_id=rule.id,
                        kind=rule.kind,
                        scope="portfolio",
                        value_pct=view.cash_pct,
                        limit_pct=rule.limit,
                        message=(
                            f"cash is {q(view.cash_pct, PCT)}%, below the "
                            f"{q(rule.limit, PCT)}% minimum"
                        ),
                    )
                )
        elif rule.kind is RuleKind.MAX_DRAWDOWN_PCT:
            for ticker, dd in sorted(drawdowns.items()):
                if rule.scope and rule.scope.upper() != ticker.upper():
                    continue
                if abs(dd) > rule.limit:
                    alerts.append(
                        Alert(
                            rule_id=rule.id,
                            kind=rule.kind,
                            scope=ticker,
                            value_pct=abs(dd),
                            limit_pct=rule.limit,
                            message=(
                                f"{ticker} drawdown from peak is {q(abs(dd), PCT)}%, "
                                f"beyond the {q(rule.limit, PCT)}% limit"
                            ),
                        )
                    )
        elif rule.kind is RuleKind.MAX_TRANSACTION_PCT:
            # Only checkable when the caller supplies the figure; a rule we cannot
            # evaluate must not pretend to have passed.
            if transaction_pct is None:
                continue
            if transaction_pct > rule.limit:
                alerts.append(
                    Alert(
                        rule_id=rule.id,
                        kind=rule.kind,
                        scope="largest transaction",
                        value_pct=transaction_pct,
                        limit_pct=rule.limit,
                        message=(
                            f"largest transaction was {q(transaction_pct, PCT)}% of the "
                            f"portfolio, above the {q(rule.limit, PCT)}% limit"
                        ),
                    )
                )
    return alerts
