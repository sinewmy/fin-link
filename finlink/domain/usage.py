"""Usage instrumentation for product doc §16.

The product doc's success measures are about *process quality*, not returns: did
the user record a reason, did they define what would prove them wrong, did they
re-check after major news? All computed from the workspace; no model involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from finlink.models import LedgerRow, Side, ThesisFrontmatter

ZERO = Decimal("0")


@dataclass(frozen=True)
class UsageMetrics:
    transactions: int = 0
    with_reason: int = 0
    with_invalidation: int = 0
    with_thesis: int = 0
    theses_total: int = 0
    theses_validated: int = 0
    theses_expired: int = 0
    theses_challenged: int = 0
    theses_invalidated: int = 0

    @property
    def pct_with_reason(self) -> Decimal:
        return _pct(self.with_reason, self.transactions)

    @property
    def pct_with_invalidation(self) -> Decimal:
        return _pct(self.with_invalidation, self.theses_total)

    @property
    def pct_validated(self) -> Decimal:
        return _pct(self.theses_validated, self.theses_total)

    def render(self) -> str:
        return "\n".join(
            [
                f"- transactions recorded: {self.transactions}",
                f"- with a stated reason: {self.pct_with_reason}% "
                f"({self.with_reason}/{self.transactions})",
                f"- theses with invalidation conditions: {self.pct_with_invalidation}% "
                f"({self.with_invalidation}/{self.theses_total})",
                f"- theses re-validated at least once: {self.pct_validated}% "
                f"({self.theses_validated}/{self.theses_total})",
                (
                    f"- theses challenged: {self.theses_challenged}, "
                    f"invalidated: {self.theses_invalidated}, "
                    f"past horizon: {self.theses_expired}"
                ),
            ]
        )


def _pct(part: int, whole: int) -> Decimal:
    if whole == 0:
        return ZERO
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1"))


def compute(
    ledger: list[LedgerRow],
    theses: list[tuple[Path, ThesisFrontmatter]],
    *,
    as_of: date | None = None,
) -> UsageMetrics:
    as_of = as_of or date.today()
    slugs = {tf.slug for _, tf in theses}
    linked = {r.thesis_slug for r in ledger if r.thesis_slug} & slugs

    validated = 0
    expired = 0
    for _path, tf in theses:
        if tf.last_validated is not None:
            validated += 1
        if any(h.status.value == "expired" for h in tf.hypotheses):
            expired += 1

    return UsageMetrics(
        transactions=len(ledger),
        with_reason=sum(1 for r in ledger if r.reason.strip()),
        # Invalidation conditions live on the thesis; a buy with no thesis cannot
        # have defined what would prove it wrong.
        with_invalidation=sum(1 for _p, tf in theses if tf.invalidation_conditions),
        with_thesis=len(linked),
        theses_total=len(theses),
        theses_validated=validated,
        theses_expired=expired,
        theses_challenged=sum(1 for _p, tf in theses if tf.status.value == "challenged"),
        theses_invalidated=sum(1 for _p, tf in theses if tf.status.value == "invalidated"),
    )


def buys_without_reason(ledger: list[LedgerRow]) -> list[LedgerRow]:
    return [r for r in ledger if r.side is Side.BUY and not r.reason.strip()]
