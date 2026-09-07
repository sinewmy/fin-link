"""Pipeline P3 — validation. The core value loop.

Structure (TECHNICAL_DESIGN 4.5 / 5-P3):
  1. deterministic pre-filter of cached news
  2. Pass A — supporting evidence
  3. Pass B — contrary evidence, INDEPENDENT: Pass A's output is never shown to it
  4. portfolio context computed by domain/ (never by the model)
  5. synthesis, with a numeric audit of position_note
  6. append one `## Validation - <date>` section; key-scoped frontmatter update
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finlink.domain.context import NoContext, PortfolioContext
from finlink.domain.horizon import is_expired
from finlink.domain.relevance import Candidate, prefilter_metrics
from finlink.domain.risk import Alert
from finlink.ingest.base import NewsItem
from finlink.io.markdown import append_section, read_document, set_frontmatter_key
from finlink.llm.base import LLMMetadata, sha256
from finlink.llm.client import LLMClient
from finlink.llm.schemas import EvidenceItem, EvidencePass, Synthesis, Verdict

PASS_A = "P3_validate_pass_a"
PASS_B = "P3_validate_pass_b"
SYNTH = "P3_validate_synthesis"

EVIDENCE_MARKER = "<!-- evidence-digest: {digest} -->"


@dataclass
class ValidateInput:
    thesis_path: Path
    ticker: str
    hypotheses: list[dict[str, str]]
    created: date
    horizon: str = ""
    news: list[NewsItem] = field(default_factory=list)
    as_of: date | None = None
    context: PortfolioContext | NoContext | None = None
    alerts: list[Alert] = field(default_factory=list)


@dataclass
class ValidationResult:
    path: Path
    supporting: EvidencePass
    contrary: EvidencePass
    synthesis: Synthesis
    candidates: list[Candidate]
    section: str
    expired_ids: list[str]
    context_line: str
    metas: list[LLMMetadata] = field(default_factory=list)


def _render_hypotheses(hypotheses: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for h in hypotheses:
        kind = h.get("kind") or "sub"
        hid = h.get("id") or "?"
        lines.append(
            f"- {hid} [{kind}] {h.get('statement', '')}\n"
            f"  observable: {h.get('observable_metric', '')}"
        )
    return "\n".join(lines)


def _render_evidence_items(candidates: list[Candidate]) -> str:
    if not candidates:
        return "(no evidence items were selected by the deterministic pre-filter)"
    out: list[str] = []
    for i, c in enumerate(candidates, start=1):
        it = c.item
        out.append(
            f"[{i}] title: {it.title}\n"
            f"    url: {it.url}\n"
            f"    published_at: {it.published_at}\n"
            f"    source: {it.source}\n"
            f"    summary: {it.summary}"
        )
    return "\n".join(out)


def evidence_digest(candidates: list[Candidate]) -> str:
    """Stable fingerprint of the evidence set, stored in the appended section."""
    parts = sorted(f"{c.item.url}|{c.item.published_at}" for c in candidates)
    return sha256("\n".join(parts))


def run_pass_a(
    inp: ValidateInput, candidates: list[Candidate], client: LLMClient, model: str | None
) -> tuple[EvidencePass, LLMMetadata]:
    res, meta = client.run(
        pipeline=PASS_A,
        prompt_name="validate_supporting_v1",
        schema=EvidencePass,
        variables={
            "ticker": inp.ticker,
            "hypotheses": _render_hypotheses(inp.hypotheses),
            "user_prompt": (
                f"TICKER: {inp.ticker}\n\n"
                f"HYPOTHESES:\n{_render_hypotheses(inp.hypotheses)}\n\n"
                f"EVIDENCE ITEMS:\n{_render_evidence_items(candidates)}\n"
            ),
        },
        model=model,
    )
    if res.direction != "supporting":
        raise RuntimeError(f"pass A returned direction {res.direction!r}, expected 'supporting'")
    return res, meta


def run_pass_b(
    inp: ValidateInput, candidates: list[Candidate], client: LLMClient, model: str | None
) -> tuple[EvidencePass, LLMMetadata]:
    """Pass B receives ONLY the raw candidates — never Pass A's output.

    This isolation is the point of the two-pass design; anything from Pass A leaking
    in here would let the counter-case anchor on the supporting case.
    """
    res, meta = client.run(
        pipeline=PASS_B,
        prompt_name="validate_contrary_v1",
        schema=EvidencePass,
        variables={
            "ticker": inp.ticker,
            "hypotheses": _render_hypotheses(inp.hypotheses),
            "user_prompt": (
                f"TICKER: {inp.ticker}\n\n"
                f"HYPOTHESES:\n{_render_hypotheses(inp.hypotheses)}\n\n"
                f"EVIDENCE ITEMS:\n{_render_evidence_items(candidates)}\n"
            ),
        },
        model=model,
    )
    if res.direction != "contrary":
        raise RuntimeError(f"pass B returned direction {res.direction!r}, expected 'contrary'")
    return res, meta


def run_synthesis(
    inp: ValidateInput,
    supporting: EvidencePass,
    contrary: EvidencePass,
    client: LLMClient,
    model: str | None,
) -> tuple[Synthesis, LLMMetadata]:
    context = inp.context or NoContext()
    context_line = context.render()
    res, meta = client.run(
        pipeline=SYNTH,
        prompt_name="validate_synthesis_v1",
        schema=Synthesis,
        variables={
            "ticker": inp.ticker,
            "portfolio_context": context_line,
            "user_prompt": (
                f"TICKER: {inp.ticker}\n\n"
                f"PORTFOLIO CONTEXT:\n{context_line}\n\n"
                f"SUPPORTING EVIDENCE ({len(supporting.items)}):\n"
                f"{_render_pass_items(supporting.items)}\n\n"
                f"CONTRARY EVIDENCE ({len(contrary.items)}):\n"
                f"{_render_pass_items(contrary.items)}\n"
            ),
        },
        model=model,
    )
    # The numeric audit: a model may interpret the computed context, not extend it.
    res.check_position_note_numbers(context.allowed_numbers)
    if res.supporting_count != len(supporting.items):
        raise RuntimeError(
            f"synthesis reports {res.supporting_count} supporting items but pass A returned "
            f"{len(supporting.items)}"
        )
    if res.contrary_count != len(contrary.items):
        raise RuntimeError(
            f"synthesis reports {res.contrary_count} contrary items but pass B returned "
            f"{len(contrary.items)}"
        )
    return res, meta


def _render_pass_items(items: list[EvidenceItem]) -> str:
    if not items:
        return "(none)"
    return "\n".join(
        f"- [{it.strength.value}] {it.claim} — {it.url} ({it.published_at})\n  because: {it.why}"
        for it in items
    )


def _render_section(
    inp: ValidateInput,
    supporting: EvidencePass,
    contrary: EvidencePass,
    synth: Synthesis,
    candidates: list[Candidate],
    context_line: str,
    day: date,
) -> str:
    lines: list[str] = []
    digest = evidence_digest(candidates)
    lines.append(f"<!-- validation date: {day.isoformat()} | candidates: {len(candidates)} -->")
    lines.append(EVIDENCE_MARKER.format(digest=digest))
    lines.append("")
    lines.append("### Supporting")
    lines.append("")
    if supporting.items:
        for it in supporting.items:
            lines.append(
                f"- {it.claim} — [{it.source or 'source'}]({it.url}) "
                f"({it.published_at}) strength={it.strength.value}"
            )
            if it.why:
                lines.append(f"  - {it.why}")
    else:
        lines.append(f"- none found — {supporting.not_found_reason or 'no reason given'}")
    lines += ["", "### Contrary", ""]
    if contrary.items:
        for it in contrary.items:
            lines.append(
                f"- {it.claim} — [{it.source or 'source'}]({it.url}) "
                f"({it.published_at}) strength={it.strength.value}"
            )
            if it.why:
                lines.append(f"  - {it.why}")
    else:
        lines.append(
            f"- none found — {contrary.not_found_reason or 'no reason given'} "
            "(no contrary evidence was found; this is not evidence of validity)"
        )
    lines += ["", "### Portfolio context", "", context_line, ""]
    lines += ["### Verdict", "", f"{synth.verdict.value} — confidence {synth.confidence.value}", ""]
    lines.append(synth.rationale)
    lines += ["", f"> Position: {synth.position_note}", ""]
    lines += ["### Uncertainty", "", synth.uncertainty, ""]
    return "\n".join(lines)


def expired_hypotheses(inp: ValidateInput, day: date) -> list[str]:
    """Hypotheses whose horizon has passed. Expired is not the same as supported."""
    if not inp.horizon:
        return []
    return [
        str(h.get("id"))
        for h in inp.hypotheses
        if h.get("id")
        and is_expired(inp.created, inp.horizon, day)
        and str(h.get("status")) not in ("invalidated", "expired")
    ]


def apply_expiry(path: Path, ids: list[str]) -> None:
    """Mark expired hypotheses in frontmatter only — the body is never rewritten."""
    if not ids:
        return
    doc = read_document(path)
    raw = doc.frontmatter.get("hypotheses") or []
    raw_list: list[object] = list(raw) if isinstance(raw, (list, tuple)) else []
    hyps: list[dict[str, object]] = [h for h in raw_list if isinstance(h, dict)]
    changed = False
    for h in hyps:
        if str(h.get("id")) in ids:
            h["status"] = "expired"
            changed = True
    if changed:
        set_frontmatter_key(path, "hypotheses", hyps)


def run(
    inp: ValidateInput,
    *,
    client: LLMClient,
    model_a: str | None = None,
    model_b: str | None = None,
    model_s: str | None = None,
    min_score: float = 0.10,
) -> ValidationResult:
    day = inp.as_of or date.today()
    metrics = [str(h.get("observable_metric") or "") for h in inp.hypotheses]
    candidates = prefilter_metrics(
        inp.news,
        ticker=inp.ticker,
        since=inp.created.isoformat(),
        observable_metrics=metrics,
        min_score=min_score,
    )
    supporting, meta_a = run_pass_a(inp, candidates, client, model_a)
    contrary, meta_b = run_pass_b(inp, candidates, client, model_b)
    synth, meta_s = run_synthesis(inp, supporting, contrary, client, model_s)

    context = inp.context or NoContext()
    section = _render_section(inp, supporting, contrary, synth, candidates, context.render(), day)
    return ValidationResult(
        path=inp.thesis_path,
        supporting=supporting,
        contrary=contrary,
        synthesis=synth,
        candidates=candidates,
        section=section,
        expired_ids=expired_hypotheses(inp, day),
        context_line=context.render(),
        metas=[meta_a, meta_b, meta_s],
    )


VERDICT_TO_STATUS = {
    Verdict.STILL_VALID: "active",
    Verdict.PARTIALLY_VALID: "challenged",
    Verdict.CHALLENGED: "challenged",
    Verdict.INVALIDATED: "invalidated",
    Verdict.UNDETERMINED: "active",
}


def write_result(result: ValidationResult, day: date) -> None:
    """Append the validation section and update named frontmatter keys only.

    APPEND-ONLY: existing body text is never rewritten. Hand-written evidence in the
    file is byte-identical afterwards — that guarantee is the reason this exists.
    """
    append_section(result.path, f"Validation — {day.isoformat()}", result.section)
    apply_expiry(result.path, result.expired_ids)
    set_frontmatter_key(result.path, "status", VERDICT_TO_STATUS[result.synthesis.verdict])
    set_frontmatter_key(result.path, "confidence", result.synthesis.confidence.value)
    set_frontmatter_key(result.path, "last_validated", day.isoformat())


def validate(
    inp: ValidateInput,
    *,
    client: LLMClient,
    model_a: str | None = None,
    model_b: str | None = None,
    model_s: str | None = None,
    min_score: float = 0.10,
    write: bool = True,
) -> ValidationResult:
    """Run P3 and, by default, persist it. Split out from `run` so tests can assert
    on the result without touching the filesystem."""
    result = run(
        inp,
        client=client,
        model_a=model_a,
        model_b=model_b,
        model_s=model_s,
        min_score=min_score,
    )
    if write:
        write_result(result, inp.as_of or date.today())
    return result
