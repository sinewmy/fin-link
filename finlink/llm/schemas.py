"""Pydantic contracts for every LLM interaction.

These ARE the API. No pipeline may accept free-text output; if prose is needed it
is a field in one of these models.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecomposedHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable short id, e.g. h1, h2", pattern=r"^h\d+$")
    kind: str = Field(description="core or sub", pattern="^(core|sub)$")
    parent: str | None = Field(default=None, description="Parent hypothesis id for sub-hypotheses")
    statement: str = Field(min_length=1, description="One falsifiable claim, not a conclusion")
    observable_metric: str = Field(
        min_length=1,
        description="The concrete metric or datapoint that would confirm or refute this claim",
    )


class ThesisDecomposition(BaseModel):
    """Output of P1. The model structures the user's reasoning; it gives no opinion."""

    model_config = ConfigDict(extra="forbid")

    thesis_statement: str = Field(
        min_length=1, description="Restate the user's core thesis in one sentence"
    )
    hypotheses: list[DecomposedHypothesis] = Field(min_length=2, max_length=6)
    invalidation_conditions: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Observable conditions that would prove the thesis wrong",
    )
    horizon_suggestion: str = Field(
        default="", description="e.g. 2y, 6m — only if the user implied one"
    )

    @model_validator(mode="after")
    def _exactly_one_core(self) -> ThesisDecomposition:
        cores = [h for h in self.hypotheses if h.kind == "core"]
        if len(cores) != 1:
            raise ValueError(f"expected exactly 1 core hypothesis, got {len(cores)}")
        return self

    @model_validator(mode="after")
    def _valid_parents(self) -> ThesisDecomposition:
        ids = {h.id for h in self.hypotheses}
        for h in self.hypotheses:
            if h.parent is not None and h.parent not in ids:
                raise ValueError(f"hypothesis {h.id} references unknown parent {h.parent!r}")
            if h.parent == h.id:
                raise ValueError(f"hypothesis {h.id} is its own parent")
        for h in self.hypotheses:
            if h.kind == "sub" and h.parent is None:
                raise ValueError(f"sub-hypothesis {h.id} must declare a parent")
        return self

    @model_validator(mode="after")
    def _unique_ids(self) -> ThesisDecomposition:
        ids = [h.id for h in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate hypothesis ids: {ids}")
        return self
