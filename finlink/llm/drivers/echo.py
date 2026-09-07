"""Echo driver — deterministic fixtures, zero API cost.

Every pipeline MUST be runnable with this driver, otherwise it cannot be tested
without spending money. It derives output from the input so tests stay meaningful.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from finlink.llm.base import LLMError, LLMMetadata
from finlink.llm.schemas import (
    Confidence,
    DecomposedHypothesis,
    EvidenceItem,
    EvidencePass,
    EvidenceStrength,
    ReviewNarrative,
    SlugSuggestion,
    Synthesis,
    ThesisDecomposition,
    Verdict,
)

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
        result: Any
        if schema is ThesisDecomposition:
            result = _decompose_from_text(user)
            assert isinstance(result, schema)
            return result, meta
        if schema is EvidencePass:
            result = _evidence_pass(system, user)
            assert isinstance(result, schema)
            return result, meta
        if schema is Synthesis:
            result = _synthesis(user)
            assert isinstance(result, schema)
            return result, meta
        if schema is ReviewNarrative:
            result = _review(user)
            assert isinstance(result, schema)
            return result, meta
        if schema is SlugSuggestion:
            result = _slug(user)
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


_EVIDENCE_BLOCK = re.compile(
    r"\[(\d+)\] title: (?P<title>.*?)\n"
    r"\s*url: (?P<url>.*?)\n"
    r"\s*published_at: (?P<published>.*?)\n"
    r"\s*source: (?P<source>.*?)\n"
    r"\s*summary: (?P<summary>.*?)$",
    re.MULTILINE,
)


def _parse_evidence_items(user: str) -> list[dict[str, str]]:
    clean = RETRY_NOISE.sub("", user)
    return [
        {
            "title": m.group("title").strip(),
            "url": m.group("url").strip(),
            "published_at": m.group("published").strip(),
            "source": m.group("source").strip(),
            "summary": m.group("summary").strip(),
        }
        for m in _EVIDENCE_BLOCK.finditer(clean)
    ]


def _hypothesis_ids(user: str) -> list[str]:
    clean = RETRY_NOISE.sub("", user)
    ids = re.findall(r"^- (h\d+) \[", clean, re.MULTILINE)
    return ids or ["h1"]


def _evidence_pass(system: str, user: str) -> EvidencePass:
    """Derive an evidence pass from the supplied items.

    The direction is taken from the PROMPT (system), not guessed from the user text:
    Pass A and Pass B get identical user content, so only the system prompt distinguishes
    them. That is exactly the isolation the two-pass design relies on.
    """
    direction = "contrary" if "adversarial" in system.lower() else "supporting"
    items = _parse_evidence_items(user)
    ids = _hypothesis_ids(user)
    if not items:
        return EvidencePass(
            direction=direction,
            items=[],
            considered=0,
            not_found_reason=(
                "the deterministic pre-filter selected no evidence items, so none could be assessed"
            ),
        )
    # Split the candidate list so the two passes do not return identical sets, which
    # would make the "independent passes" tests vacuous. With a single candidate the
    # contrary pass legitimately gets nothing — that must fall through to the honest
    # "looked and found nothing" branch below, not raise on a missing reason.
    picked = items[1::2] if direction == "contrary" else items[0::2]
    if direction == "supporting" and not picked:
        picked = items[:1]
    if not picked:
        return EvidencePass(
            direction=direction,
            items=[],
            considered=len(items),
            not_found_reason=(
                f"reviewed {len(items)} candidate item(s) and found none that "
                f"{'undermine' if direction == 'contrary' else 'support'} the hypotheses"
            ),
        )
    out = [
        EvidenceItem(
            hypothesis_id=ids[i % len(ids)],
            claim=_frame(direction, it["title"]),
            url=it["url"],  # copied verbatim — provenance is never invented
            published_at=it["published_at"],
            strength=EvidenceStrength.STRONG if i == 0 else EvidenceStrength.MODERATE,
            source=it["source"],
            why=_why(direction, it["summary"]),
        )
        for i, it in enumerate(picked)
    ]
    return EvidencePass(direction=direction, items=out, considered=len(items))


def _frame(direction: str, title: str) -> str:
    if direction == "contrary":
        return f"this cuts against the thesis: {title}"
    return f"this supports the thesis: {title}"


def _why(direction: str, summary: str) -> str:
    head = (summary or "the item").strip().rstrip(".")
    if direction == "contrary":
        return f"it weakens the expectation because {head}"
    return f"it is consistent with the expectation because {head}"


_COUNT_RE = re.compile(r"(SUPPORTING|CONTRARY) EVIDENCE \((\d+)\)", re.IGNORECASE)
_CONTEXT_RE = re.compile(r"PORTFOLIO CONTEXT:\n(?P<ctx>.*?)\n\n", re.DOTALL)


def _synthesis(user: str) -> Synthesis:
    """Deterministic verdict from the two counts.

    Deliberately conservative: with no contrary evidence the verdict is UNDETERMINED,
    never still_valid. That mirrors the schema rule so the offline path exercises it.
    """
    clean = RETRY_NOISE.sub("", user)
    counts = {kind.lower(): int(n) for kind, n in _COUNT_RE.findall(clean)}
    supporting_n = counts.get("supporting", 0)
    contrary_n = counts.get("contrary", 0)

    m = _CONTEXT_RE.search(clean)
    context_line = m.group("ctx").strip() if m else "unavailable"
    # Match the explicit LIMIT marker, not the word "breached": the context line
    # also reads "no limits breached" when everything is inside its limits.
    breached = bool(re.search(r"LIMIT\s+\S+.*?BREACHED", context_line, re.IGNORECASE))

    if contrary_n == 0:
        verdict = Verdict.UNDETERMINED
    elif contrary_n >= supporting_n:
        verdict = Verdict.CHALLENGED
    else:
        verdict = Verdict.PARTIALLY_VALID

    if breached:
        note = (
            "the thesis has to be read against a portfolio limit that is already breached "
            "for this name, so validity is not a reason to add"
        )
    elif context_line.startswith("unavailable") or context_line.startswith("no position"):
        note = "no priced position in this name, so portfolio sizing does not constrain the thesis"
    else:
        note = "the position sits inside its portfolio limits, so sizing is not the constraint here"

    return Synthesis(
        verdict=verdict,
        confidence=Confidence.MEDIUM if contrary_n else Confidence.LOW,
        rationale=(
            f"{supporting_n} supporting and {contrary_n} contrary items were found by two "
            "independent passes."
        ),
        position_note=note,
        uncertainty=(
            "the evidence set is limited to the cached items that passed the deterministic "
            "pre-filter; uncached sources and anything behind the horizon were not assessed"
        ),
        supporting_count=supporting_n,
        contrary_count=contrary_n,
    )


_PERIOD_RE = re.compile(r"Trades in period: (\d+)")
_CONFLICT_RE = re.compile(r"### Thesis-vs-portfolio conflicts\n\n(.*?)\n\n", re.DOTALL)


def _review(user: str) -> ReviewNarrative:
    """Interpret the supplied facts without introducing any new figure.

    Deliberately conservative: it describes structure rather than inventing insight,
    and it quotes only what the computed half contained.
    """
    clean = RETRY_NOISE.sub("", user)
    m = _PERIOD_RE.search(clean)
    trades = int(m.group(1)) if m else 0
    conflict_block = _CONFLICT_RE.search(clean)
    conflicts = []
    if conflict_block:
        conflicts = [
            ln.strip().lstrip("- ").strip()
            for ln in conflict_block.group(1).splitlines()
            if ln.strip().startswith("-")
        ]
    real_conflicts = [c for c in conflicts if not c.lower().startswith("none")]

    if trades == 0:
        individual = (
            "No trades were recorded in this period, so there is no decision to review. "
            "The absence of activity is itself worth noting only if it was unintentional."
        )
    else:
        individual = (
            f"{trades} trade(s) were recorded in this period. Compare each against the "
            "reason recorded at the time: the question is whether the outcome followed "
            "from the stated reasoning or from something that was not part of it."
        )
    if real_conflicts:
        portfolio = (
            "At least one thesis remains valid while its position breaches a portfolio "
            "limit. Those are separate questions: a thesis being intact says nothing "
            "about whether the position is the right size."
        )
    else:
        portfolio = (
            "No thesis sat on a position that breached a limit this period, so sizing "
            "and conviction did not conflict."
        )
    patterns: list[str] = []
    pblock = re.search(r"### Behavioural patterns \(computed\)\n\n(.*?)\n\n", clean, re.DOTALL)
    if pblock:
        patterns = [
            ln.strip().lstrip("- ").strip()
            for ln in pblock.group(1).splitlines()
            if ln.strip().startswith("-")
        ]
        patterns = [p for p in patterns if not p.lower().startswith("no recurring")]
    return ReviewNarrative(
        individual=individual,
        portfolio=portfolio,
        patterns=patterns,
        what_i_got_wrong=[],
        next_actions=[],
        uncertainty=(
            "this reads only what the workspace recorded; unrecorded reasoning, "
            "untracked positions and anything not ingested cannot be assessed"
        ),
    )


# Enough vocabulary to prove the pipeline end-to-end offline: the point of the
# fixture is that a Chinese reason comes back as readable English words, which is
# exactly the case the user asked about.
SLUG_WORDS = {
    "腾讯": "tencent",
    "基本面": "fundamentals",
    "小米": "xiaomi",
    "低估": "undervalued",
    "股价": "valuation",
    "生物制药": "biotech",
    "中芯国际": "smic",
    "芯片": "semiconductor",
    "中国": "china",
    "发展": "growth",
}


def _slug(user: str) -> SlugSuggestion:
    reason = _extract_reason(user)
    # Longest-first so '中芯国际' wins over a shorter overlapping key.
    words = [w for k, w in sorted(SLUG_WORDS.items(), key=lambda kv: -len(kv[0])) if k in reason]
    if not words:
        # ASCII reasons: reuse the deterministic slug rule so the fixture stays
        # stable and idempotent rather than inventing a second wording.
        from finlink.domain.slug import slugify

        words = slugify(reason).split("-") or ["thesis"]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return SlugSuggestion(
        keywords=" ".join(seen[:4]),
        rationale="echo fixture: vocabulary lookup, not a model judgement",
    )


def validate_or_raise(schema: type[BaseModel], payload: str) -> BaseModel:
    try:
        return schema.model_validate_json(payload)
    except ValidationError as e:
        raise LLMError(str(e)) from e
