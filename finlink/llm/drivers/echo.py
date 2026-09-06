"""Echo driver — deterministic fixtures, zero API cost.

Every pipeline MUST be runnable with this driver, otherwise it cannot be tested
without spending money. It derives output from the input so tests stay meaningful.
"""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from finlink.llm.base import LLMError, LLMMetadata
from finlink.llm.schemas import DecomposedHypothesis, ThesisDecomposition

S = TypeVar("S", bound=BaseModel)


class EchoDriver:
    name = "echo"

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[S],
        model: str | None = None,
    ) -> tuple[S, LLMMetadata]:
        meta = LLMMetadata(driver=self.name, model=model or "echo", tokens_in=0, tokens_out=0)
        if schema is ThesisDecomposition:
            result = _decompose_from_text(user)
            assert isinstance(result, schema)
            return result, meta
        raise LLMError(f"echo driver has no fixture for schema {schema.__name__}")


def _decompose_from_text(user: str) -> ThesisDecomposition:
    """Build a plausible decomposition from the user's own words.

    Deliberately mechanical: it splits the reason into clauses so tests can assert
    structure (1 core + N subs, unique ids, observable metrics) without an API call.
    """
    reason = _extract_reason(user)
    core = reason.strip().rstrip(".")
    clauses = [c.strip() for c in re.split(r"[;.]|\bbecause\b|,", reason) if c.strip()]
    subs = clauses[1:4] or ["the trend continues"]

    hypotheses = [
        DecomposedHypothesis(
            id="h1",
            kind="core",
            statement=core[:200],
            observable_metric=f"evidence related to: {core[:80]}",
        )
    ]
    for i, clause in enumerate(subs, start=2):
        hypotheses.append(
            DecomposedHypothesis(
                id=f"h{i}",
                kind="sub",
                parent="h1",
                statement=clause[:200],
                observable_metric=f"metric tracking: {clause[:80]}",
            )
        )
    return ThesisDecomposition(
        thesis_statement=core[:300],
        hypotheses=hypotheses,
        invalidation_conditions=[f"evidence contradicting: {core[:100]}"],
        horizon_suggestion="",
    )


RETRY_NOISE = re.compile(r"\[system: previous output was rejected.*?\]", re.DOTALL)


def _extract_reason(user: str) -> str:
    """Pull the user's reason out of the prompt, stripping any retry scaffolding.

    Retry metadata must never be echoed back into generated content — that is exactly
    how a correction message would end up stored in a thesis body.
    """
    clean = RETRY_NOISE.sub("", user)
    m = re.search(r"^REASON:\s*(.+)$", clean, re.MULTILINE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return clean.strip()


def validate_or_raise(schema: type[BaseModel], payload: str) -> BaseModel:
    try:
        return schema.model_validate_json(payload)
    except ValidationError as e:
        raise LLMError(str(e)) from e
