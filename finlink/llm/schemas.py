"""Pydantic contracts for every LLM interaction.

These ARE the API. No pipeline may accept free-text output; if prose is needed it
is a field in one of these models.
"""

from __future__ import annotations

import re
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


class EvidenceStrength(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class Verdict(StrEnum):
    STILL_VALID = "still_valid"
    PARTIALLY_VALID = "partially_valid"
    CHALLENGED = "challenged"
    INVALIDATED = "invalidated"
    UNDETERMINED = "undetermined"


class EvidenceItem(BaseModel):
    """One piece of evidence. `url` and `published_at` are mandatory — provenance or nothing."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(min_length=1, description="Which hypothesis this bears on")
    claim: str = Field(min_length=1)
    url: str = Field(min_length=1)
    published_at: str = Field(min_length=1, description="ISO date of the source")
    source: str = Field(default="", description="Publication name, if the item carried one")
    strength: EvidenceStrength = EvidenceStrength.MODERATE
    why: str = Field(default="", description="One sentence on how this bears on the hypothesis")

    @model_validator(mode="after")
    def _url_is_resolvable(self) -> EvidenceItem:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"evidence url must be http(s), got {self.url!r}")
        return self


class SlugSuggestion(BaseModel):
    """Output of `improve-slugs` — the human-readable WORDS, never the slug itself.

    The model chooses wording; `domain/slug.py` derives the actual slug. That split
    keeps filenames deterministic and link-safe while still letting a model make
    `cjk-770b597d817e` readable.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: str = Field(
        min_length=1,
        max_length=120,
        description="2-5 lowercase English words naming the thesis, hyphen-free",
    )
    rationale: str = Field(default="", description="Why these words name the thesis")

    @model_validator(mode="after")
    def _keywords_must_be_romanisable(self) -> SlugSuggestion:
        if not any(ch.isascii() and ch.isalnum() for ch in self.keywords):
            raise ValueError(
                "keywords must contain at least one ASCII letter or digit; "
                "a non-Latin suggestion cannot produce a readable slug"
            )
        return self


class EvidencePass(BaseModel):
    """Output of a single evidence pass.

    Pass A produces supporting evidence, Pass B contrary. They are separate calls so
    that Pass B cannot see — and therefore cannot anchor on — Pass A's conclusions.
    """

    model_config = ConfigDict(extra="forbid")

    direction: str = Field(pattern="^(supporting|contrary)$")
    items: list[EvidenceItem] = Field(default_factory=list)
    considered: int = Field(default=0, description="How many candidate items were reviewed")
    not_found_reason: str = Field(
        default="", description="Required when no evidence of this direction was found"
    )

    @model_validator(mode="after")
    def _empty_needs_reason(self) -> EvidencePass:
        if not self.items and not self.not_found_reason.strip():
            raise ValueError(
                f"{self.direction} pass returned no evidence and no not_found_reason; "
                "an honest 'I looked and found nothing' beats silence"
            )
        return self


class Synthesis(BaseModel):
    """Verdict over the two evidence passes.

    The bias guard lives here: a thesis cannot be declared still-valid on an empty
    contrary pass. If no counter-evidence was found, the verdict must be UNDETERMINED.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    confidence: Confidence
    rationale: str = Field(min_length=1)
    position_note: str = Field(
        min_length=1,
        description="One sentence interpreting the supplied portfolio numbers. No new numbers.",
    )
    uncertainty: str = Field(
        min_length=1, description="What could NOT be determined from the available evidence"
    )
    supporting_count: int = Field(ge=0)
    contrary_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _still_valid_needs_contrary_evidence(self) -> Synthesis:
        if self.verdict is Verdict.STILL_VALID and self.contrary_count == 0:
            raise ValueError(
                "verdict 'still_valid' with zero contrary evidence is rejected: "
                "you cannot claim a thesis holds without having looked for the counter-case"
            )
        return self

    @model_validator(mode="after")
    def _counts_match(self) -> Synthesis:
        if self.verdict is Verdict.STILL_VALID and self.supporting_count == 0:
            raise ValueError(
                "verdict 'still_valid' with zero supporting evidence: nothing supports it"
            )
        return self

    def check_position_note_numbers(
        self, allowed: set[str], tickers: set[str] | None = None
    ) -> None:
        """Any number in `position_note` absent from the computed context fails.

        This is the enforceable half of 'the model may interpret but not recompute'.
        Ticker tokens (e.g. 00700.HK) are stripped first: a ticker is a name, not
        portfolio math — its digits must not trip the numeric audit.
        """
        text = self.position_note
        for t in tickers or ():
            text = text.replace(t, "")
        unknown = [n for n in _numbers(text) if n not in allowed]
        if unknown:
            raise ValueError(
                "position_note contains numbers absent from the computed portfolio context: "
                + ", ".join(sorted(unknown))
                + ". The model may interpret the supplied figures, not invent new ones."
            )


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    """Extract numeric tokens, normalised so 22.30 and 22.3 compare equal."""
    out: set[str] = set()
    for raw in _NUMBER.findall(text or ""):
        v = raw.replace(",", "")
        if "." in v:
            v = v.rstrip("0").rstrip(".")
        out.add(v or "0")
    return out


class ReviewNarrative(BaseModel):
    """Output of P4 — the prose half of the review, and ONLY the prose half.

    Every number in the report is computed by `domain/` and written by code. The
    model contributes interpretation and the patterns it can see in that material.
    """

    model_config = ConfigDict(extra="forbid")

    individual: str = Field(
        min_length=1,
        description="Decisions, expectations, outcomes, where I was wrong, recurring patterns",
    )
    portfolio: str = Field(
        min_length=1,
        description="Reading of the drift, concentration, return and conflicts supplied",
    )
    patterns: list[str] = Field(
        default_factory=list, description="Concrete behavioural patterns, one per item"
    )
    what_i_got_wrong: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    uncertainty: str = Field(min_length=1, description="What could not be determined")

    def check_numbers(self, allowed: set[str]) -> None:
        """Explicit numeric audit — every figure must trace back to `domain/`."""
        unknown: list[str] = []
        for field_name in ("individual", "portfolio"):
            unknown += [n for n in _numbers(getattr(self, field_name)) if n not in allowed]
        if unknown:
            raise ValueError(
                "review narrative contains numbers the CLI did not compute: "
                + ", ".join(sorted(set(unknown)))
            )
