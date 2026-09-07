"""Pydantic models for every markdown-tracked record.

These are the schema contract. `finlink doctor` validates all files against them
and FAILS LOUD — it never silently repairs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_CURRENCIES = ("USD", "HKD", "SEK")


class ThesisStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CHALLENGED = "challenged"
    INVALIDATED = "invalidated"
    CLOSED = "closed"


class HypothesisStatus(StrEnum):
    PENDING = "pending"
    SUPPORTED = "supported"
    CHALLENGED = "challenged"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class EvidenceDirection(StrEnum):
    SUPPORTING = "supporting"
    CONTRARY = "contrary"
    NEUTRAL = "neutral"


class Confidence(StrEnum):
    """Ordinal only — NEVER a probability, never a score out of 100."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str = Field(pattern="^(core|sub)$")
    statement: str = Field(min_length=1)
    observable_metric: str = Field(min_length=1)
    status: HypothesisStatus = HypothesisStatus.PENDING
    parent: str | None = None


class ThesisFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    status: ThesisStatus = ThesisStatus.DRAFT
    created: date
    horizon: str = Field(default="", description="e.g. 2y, 6m")
    base_currency: str = Field(default="USD")
    invalidation_conditions: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    last_validated: date | None = Field(
        default=None, description="Set by P3; never set at creation"
    )
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    @field_validator("last_validated", mode="before")
    @classmethod
    def _blank_date(cls, v: object) -> object:
        return _blank_to_none(v)

    @field_validator("base_currency")
    @classmethod
    def _ccy(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {v!r}; expected one of {SUPPORTED_CURRENCIES}")
        return v


class PositionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    quantity: Decimal
    avg_cost: Decimal
    currency: str
    opened_at: date | None = None
    thesis_slug: str | None = None

    @field_validator("opened_at", mode="before")
    @classmethod
    def _blank_date(cls, v: object) -> object:
        return _blank_to_none(v)

    notes: str = ""

    @field_validator("currency")
    @classmethod
    def _ccy(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {v!r}")
        return v


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


def _blank_to_none(v: object) -> object:
    """Markdown tables use `-` for absent values; treat as None, not as an error."""
    return None if isinstance(v, str) and v.strip() in ("", "-") else v


class LedgerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    ticker: str
    side: Side
    quantity: Decimal
    price: Decimal
    currency: str
    fees: Decimal | None = Decimal("0")
    reason: str = ""
    thesis_slug: str | None = None
    fx_rate_usd_at_trade: Decimal | None = None

    @field_validator("fx_rate_usd_at_trade", "fees", mode="before")
    @classmethod
    def _blank_number(cls, v: object) -> object:
        """Blank means 'not supplied', not zero — default it rather than erroring."""
        if isinstance(v, str) and v.strip() in ("", "-"):
            return None
        return v

    @field_validator("currency")
    @classmethod
    def _ccy(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {v!r}")
        return v


class CashEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    amount: Decimal

    @field_validator("currency")
    @classmethod
    def _ccy(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {v!r}")
        return v
