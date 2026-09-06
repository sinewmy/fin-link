from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finlink.models import (
    Confidence,
    Hypothesis,
    LedgerRow,
    PositionRow,
    ThesisFrontmatter,
    ThesisStatus,
)


def valid_fm(**over) -> dict:
    base = {
        "ticker": "NVDA",
        "slug": "ai-capex",
        "status": "active",
        "created": "2026-09-06",
        "horizon": "2y",
        "invalidation_conditions": ["capex turns negative"],
        "hypotheses": [
            {
                "id": "h1",
                "kind": "core",
                "statement": "capex grows",
                "observable_metric": "hyperscaler capex",
            }
        ],
    }
    base.update(over)
    return base


def test_thesis_roundtrip():
    tf = ThesisFrontmatter.model_validate(valid_fm())
    assert tf.status is ThesisStatus.ACTIVE
    assert tf.confidence is Confidence.MEDIUM
    assert tf.hypotheses[0].status == "pending"


def test_enum_case_drift_is_rejected():
    """Schema drift like `CHALLENGED` vs `challenged` must fail loud, not silently pass."""
    with pytest.raises(ValidationError):
        ThesisFrontmatter.model_validate(valid_fm(status="CHALLENGED"))


def test_unknown_currency_rejected():
    with pytest.raises(ValidationError, match="unsupported currency"):
        PositionRow(ticker="X", quantity=Decimal("1"), avg_cost=Decimal("1"), currency="EUR")


def test_currency_is_normalised_upper():
    row = PositionRow(ticker="X", quantity=Decimal("1"), avg_cost=Decimal("1"), currency="sek")
    assert row.currency == "SEK"


def test_unknown_field_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        ThesisFrontmatter.model_validate(valid_fm(statusx="active"))


def test_hypothesis_missing_observable_metric_rejected():
    with pytest.raises(ValidationError):
        Hypothesis(id="h1", kind="core", statement="x", observable_metric="")


def test_ledger_row_defaults():
    row = LedgerRow(
        date=date(2026, 9, 6),
        ticker="NVDA",
        side="buy",
        quantity=Decimal("10"),
        price=Decimal("175"),
        currency="USD",
    )
    assert row.fees == Decimal("0")
    assert row.reason == ""
