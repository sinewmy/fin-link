"""The alert ledger (TECHNICAL_DESIGN 4.7).

Alerts are ACKNOWLEDGED, never silently cleared, and no code path lets a model
suppress, downgrade or close one. Regenerating the file carries forward any
acknowledgement the user made; an alert that stops breaching is recorded as
resolved but is never deleted, so the history stays auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finlink.domain.risk import Alert

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RESOLVED = "resolved"

HEADERS = ["id", "rule", "scope", "observed", "limit", "raised", "status", "acked", "message"]


@dataclass(frozen=True)
class AlertRecord:
    """An alert plus its lifecycle state."""

    alert: Alert
    raised: date
    status: str = STATUS_OPEN
    acked: date | None = None

    @property
    def key(self) -> str:
        """Identity of the underlying condition, independent of its values.

        Weight can drift every run; an alert is the same alert while it refers to
        the same rule and the same thing.
        """
        return f"{self.alert.rule_id}:{self.alert.scope}"

    @property
    def id(self) -> str:
        return f"{self.key}:{self.raised.isoformat()}"

    def acknowledge(self, when: date) -> AlertRecord:
        return AlertRecord(
            alert=self.alert, raised=self.raised, status=STATUS_ACKNOWLEDGED, acked=when
        )

    def resolve(self) -> AlertRecord:
        """Condition no longer breaches. The record is kept, not dropped."""
        if self.status == STATUS_ACKNOWLEDGED:
            return self
        return AlertRecord(
            alert=self.alert, raised=self.raised, status=STATUS_RESOLVED, acked=self.acked
        )

    def with_alert(self, alert: Alert) -> AlertRecord:
        """Carry lifecycle state onto a freshly observed breach of the same condition."""
        return AlertRecord(alert=alert, raised=self.raised, status=self.status, acked=self.acked)

    @property
    def observed(self) -> Decimal:
        return self.alert.value_pct

    @property
    def limit(self) -> Decimal:
        return self.alert.limit_pct


def reconcile(previous: list[AlertRecord], current: list[Alert], today: date) -> list[AlertRecord]:
    """Merge freshly evaluated breaches into the existing ledger.

    Guarantees:
      * a condition already on file keeps its original `raised` date and its
        acknowledgement, even if the observed value has drifted;
      * an alert that no longer breaches stays in the file as `resolved`;
      * a genuinely new condition is added as `open`.
    """
    prior = {r.key: r for r in previous}
    fresh: dict[str, AlertRecord] = {r.key: r for r in _records(current, today)}

    out: list[AlertRecord] = []
    for key, rec in fresh.items():
        old = prior.get(key)
        out.append(old.with_alert(rec.alert) if old else rec)
    for key, old in prior.items():
        if key not in fresh:
            out.append(old.resolve())
    out.sort(key=lambda r: (r.status != STATUS_OPEN, r.key, r.raised))
    return out


def _records(alerts: list[Alert], today: date) -> list[AlertRecord]:
    return [AlertRecord(alert=a, raised=today) for a in alerts]


def acknowledge(records: list[AlertRecord], key: str, when: date) -> list[AlertRecord]:
    """Acknowledge by key. Returns a new list; raises if nothing matches."""
    out: list[AlertRecord] = []
    found = False
    for r in records:
        if r.key == key:
            out.append(r.acknowledge(when))
            found = True
        else:
            out.append(r)
    if not found:
        raise KeyError(f"no alert matching {key!r}")
    return out


def open_alerts(records: list[AlertRecord]) -> list[AlertRecord]:
    return [r for r in records if r.status == STATUS_OPEN]


def acknowledged_alerts(records: list[AlertRecord]) -> list[AlertRecord]:
    return [r for r in records if r.status == STATUS_ACKNOWLEDGED]


def to_rows(records: list[AlertRecord]) -> list[dict[str, str]]:
    from finlink.domain.money import q

    return [
        {
            "id": r.id,
            "rule": r.alert.rule_id,
            "scope": r.alert.scope,
            "observed": f"{q(r.observed, Decimal('0.01'))}",
            "limit": f"{q(r.limit, Decimal('0.01'))}",
            "raised": r.raised.isoformat(),
            "status": r.status,
            "acked": r.acked.isoformat() if r.acked else "-",
            "message": r.alert.message.replace("|", "\\|"),
        }
        for r in records
    ]
