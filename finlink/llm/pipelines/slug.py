"""Pipeline P6 — slug improvement.

`backfill-theses` derives slugs deterministically, which keeps links stable but can
leave an unreadable name (`cjk-770b597d817e`). This pipeline asks a model for the
WORDS, then lets `domain/slug.py` derive the slug from them.

The split is the whole point: a model proposing a filename can break every link in
positions.md on a whim. Proposing words cannot — the slug rule keeps it deterministic
and path-safe, and `is_adoptable` rejects anything that would not survive as a link.

When: run it after seeding a portfolio or recording trades in another language. It is
never automatic — a slug is a link target, and renaming one is a user decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from finlink.domain.slug import is_adoptable, slugify
from finlink.llm.base import LLMMetadata
from finlink.llm.client import LLMClient
from finlink.llm.schemas import SlugSuggestion

PIPELINE = "P6_slug"


@dataclass
class SlugInput:
    ticker: str
    reason: str
    current_slug: str = ""


@dataclass
class SlugResult:
    slug: str
    suggestion: SlugSuggestion
    meta: LLMMetadata
    adopted: bool
    reason: str


def run(
    inp: SlugInput,
    *,
    client: LLMClient,
    model: str | None = None,
) -> SlugResult:
    suggestion, meta = client.run(
        pipeline=PIPELINE,
        prompt_name="slug_v1",
        schema=SlugSuggestion,
        variables={
            "ticker": inp.ticker,
            "user_prompt": f"TICKER: {inp.ticker}\nREASON: {inp.reason}\n",
        },
        model=model,
    )
    candidate = slugify(suggestion.keywords)
    if not is_adoptable(candidate):
        return SlugResult(
            slug=inp.current_slug,
            suggestion=suggestion,
            meta=meta,
            adopted=False,
            reason=f"suggestion {suggestion.keywords!r} produced an unusable slug {candidate!r}",
        )
    if candidate == inp.current_slug:
        return SlugResult(
            slug=candidate,
            suggestion=suggestion,
            meta=meta,
            adopted=False,
            reason="already named well",
        )
    return SlugResult(
        slug=candidate,
        suggestion=suggestion,
        meta=meta,
        adopted=True,
        reason=f"{inp.current_slug} -> {candidate}",
    )
