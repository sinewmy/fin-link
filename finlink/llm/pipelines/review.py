"""Pipeline P4 — the weekly review.

Two mandatory halves (product doc Phase 4 / §8):
  1. Individual — decisions, expectations, outcomes, where I was wrong, patterns.
  2. Portfolio — weight drift, concentration and cash change, return and drawdown,
     alerts, and thesis-vs-portfolio conflicts.

Every number is computed by `domain/` and written by code. The model supplies only
the narrative, and its output is audited so it cannot introduce a new figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from finlink.domain.alerts import AlertRecord, open_alerts
from finlink.domain.drift import (
    Drift,
    Pattern,
    WeightSnapshot,
    current_week,
    diff,
    week_range,
)
from finlink.domain.money import q
from finlink.domain.usage import UsageMetrics
from finlink.io.markdown import Doc, render_document, write_atomic
from finlink.llm.base import LLMMetadata
from finlink.llm.client import LLMClient
from finlink.llm.schemas import ReviewNarrative
from finlink.models import LedgerRow, ThesisFrontmatter

PCT = Decimal("0.01")


@dataclass
class ReviewInput:
    week: str
    start: date
    end: date
    ledger: list[LedgerRow] = field(default_factory=list)
    period_trades: list[LedgerRow] = field(default_factory=list)
    theses: list[tuple[Path, ThesisFrontmatter]] = field(default_factory=list)
    start_snapshot: WeightSnapshot | None = None
    end_snapshot: WeightSnapshot | None = None
    alerts: list[AlertRecord] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    usage: UsageMetrics | None = None
    portfolio_return_pct: Decimal | None = None
    portfolio_drawdown_pct: Decimal | None = None


@dataclass
class ReviewResult:
    path: Path
    narrative: ReviewNarrative
    body: str
    allowed_numbers: set[str]
    meta: LLMMetadata


def allowed_numbers(inp: ReviewInput) -> set[str]:
    """Every numeric token the narrative is permitted to mention."""
    out: set[str] = set()
    for snapshot in (inp.start_snapshot, inp.end_snapshot):
        if snapshot is None:
            continue
        for table in (snapshot.by_ticker, snapshot.by_sector, snapshot.by_country):
            out |= {_norm(v) for v in table.values()}
        out |= {_norm(snapshot.cash_pct), _norm(snapshot.total_usd)}
    for kind in _drift_values(inp):
        out |= {_norm(kind.start_pct), _norm(kind.end_pct), _norm(kind.change_pct)}
    for a in inp.alerts:
        out |= {_norm(a.observed), _norm(a.limit)}
    if inp.portfolio_return_pct is not None:
        out |= {_norm(inp.portfolio_return_pct)}
    if inp.portfolio_drawdown_pct is not None:
        out |= {_norm(inp.portfolio_drawdown_pct)}
    out |= {str(len(inp.period_trades)), str(len(inp.ledger)), str(len(inp.theses))}
    out |= {str(len(inp.alerts)), str(len(open_alerts(inp.alerts)))}
    for line in inp.conflicts:
        out |= set(_numbers(line))
    for p in inp.patterns:
        out |= set(_numbers(p.detail))
        out |= set(_numbers(p.name))
    if inp.usage is not None:
        out |= set(_numbers(inp.usage.render()))
    return {x for x in out if x}


def _drift_values(inp: ReviewInput) -> list[Drift]:
    if inp.start_snapshot is None or inp.end_snapshot is None:
        return []
    out: list[Drift] = []
    for rows in diff(inp.start_snapshot, inp.end_snapshot).values():
        out.extend(rows)
    return out


def _norm(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _numbers(text: str) -> set[str]:
    import re

    return {
        m.replace(",", "").rstrip("0").rstrip(".") or "0"
        for m in re.findall(r"\d+(?:[.,]\d+)?", text or "")
    }


def render_facts(inp: ReviewInput) -> str:
    """The computed half of the report — written by code, never by the model."""
    lines: list[str] = [f"# Review — {inp.week} ({inp.start} → {inp.end})", ""]
    lines.append(
        "_Not investment advice. Not for execution. Figures computed by finlink; "
        "interpretation only._"
    )
    lines += ["", "## Individual", "", f"Trades in period: {len(inp.period_trades)}"]
    for r in inp.period_trades:
        lines.append(
            f"- {r.date.isoformat()} {r.side.value} {r.quantity} {r.ticker} @ {r.price} "
            f"{r.currency}"
            + (f" — reason: {r.reason}" if r.reason.strip() else " — **no reason recorded**")
        )
    if not inp.period_trades:
        lines.append("- none")

    lines += ["", "### Behavioural patterns (computed)", ""]
    if inp.patterns:
        for p in inp.patterns:
            lines.append(f"- **{p.name}** — {p.detail}")
            for e in p.evidence:
                lines.append(f"  - {e}")
    else:
        lines.append("- no recurring pattern detected in the available history")

    lines += ["", "## Portfolio", ""]
    if inp.start_snapshot is not None and inp.end_snapshot is not None:
        drifts = diff(inp.start_snapshot, inp.end_snapshot)
        for kind, label in (("position", "position"), ("sector", "sector"), ("country", "country")):
            rows = drifts.get(kind) or []
            if not rows:
                continue
            lines.append(f"### Weight drift — {label}")
            lines.append("")
            lines.append("| name | start | end | change |")
            lines.append("| --- | --- | --- | --- |")
            for d in rows:
                lines.append(
                    f"| {d.name} | {q(d.start_pct, PCT)}% | {q(d.end_pct, PCT)}% | "
                    f"{q(d.change_pct, PCT)}% |"
                )
            lines.append("")
        lines.append(
            f"Cash: {q(inp.start_snapshot.cash_pct, PCT)}% → {q(inp.end_snapshot.cash_pct, PCT)}%"
        )
        lines.append(
            f"Total value (USD): {q(inp.start_snapshot.total_usd)} → "
            f"{q(inp.end_snapshot.total_usd)}"
        )
    else:
        lines.append("_Portfolio snapshots unavailable — run `finlink ingest`._")
    if inp.portfolio_return_pct is not None:
        lines.append(f"Portfolio return over period: {q(inp.portfolio_return_pct, PCT)}%")
    if inp.portfolio_drawdown_pct is not None:
        lines.append(f"Max drawdown from peak: {q(inp.portfolio_drawdown_pct, PCT)}%")

    lines += ["", "### Alerts", ""]
    if inp.alerts:
        for a in inp.alerts:
            lines.append(
                f"- [{a.status}] {a.alert.rule_id} {a.alert.scope}: "
                f"{q(a.observed, PCT)}% vs limit {q(a.limit, PCT)}% — {a.alert.message}"
            )
    else:
        lines.append("- none")

    lines += ["", "### Thesis-vs-portfolio conflicts", ""]
    if inp.conflicts:
        for c in inp.conflicts:
            lines.append(f"- {c}")
    else:
        lines.append("- none detected")

    if inp.usage is not None:
        lines += ["", "## Process metrics (§16)", "", inp.usage.render()]
    return "\n".join(lines) + "\n"


def render(inp: ReviewInput, narrative: ReviewNarrative) -> str:
    facts = render_facts(inp)
    parts = [
        facts,
        "## Narrative (model interpretation)",
        "",
        "### Individual",
        "",
        narrative.individual,
        "",
        "### Portfolio",
        "",
        narrative.portfolio,
        "",
    ]
    if narrative.patterns:
        parts += ["### Patterns", ""]
        parts += [f"- {p}" for p in narrative.patterns]
        parts.append("")
    if narrative.what_i_got_wrong:
        parts += ["### Where I was wrong", ""]
        parts += [f"- {w}" for w in narrative.what_i_got_wrong]
        parts.append("")
    if narrative.next_actions:
        parts += ["### Next actions", ""]
        parts += [f"- {a}" for a in narrative.next_actions]
        parts.append("")
    parts += ["### Uncertainty", "", narrative.uncertainty, ""]
    return "\n".join(parts)


def build_frontmatter(inp: ReviewInput, narrative: ReviewNarrative) -> dict[str, object]:
    return {
        "week": inp.week,
        "start": inp.start.isoformat(),
        "end": inp.end.isoformat(),
        "generated": date.today().isoformat(),
        "trades_in_period": len(inp.period_trades),
        "alerts_open": len(open_alerts(inp.alerts)),
        "conflicts": len(inp.conflicts),
        "patterns": [p.name for p in inp.patterns],
    }


def run(
    inp: ReviewInput,
    *,
    client: LLMClient,
    model: str | None = None,
    reviews_dir: Path | None = None,
    write: bool = True,
) -> ReviewResult:
    allowed = allowed_numbers(inp)
    facts = render_facts(inp)
    narrative, meta = client.run(
        pipeline="P4_review",
        prompt_name="review_v1",
        schema=ReviewNarrative,
        variables={
            "week": inp.week,
            "user_prompt": (
                f"WEEK: {inp.week} ({inp.start} to {inp.end})\n\n"
                f"COMPUTED FACTS:\n{facts}\n\n"
                f"Write the narrative half only. Do not introduce any number "
                f"that does not appear above.\n"
            ),
        },
        model=model,
    )
    narrative.check_numbers(allowed)
    body = render(inp, narrative)

    path = (reviews_dir or Path("reviews")) / f"{inp.week}.md"
    if write:
        write_atomic(
            path,
            render_document(
                Doc(frontmatter=build_frontmatter(inp, narrative), body=body, path=path)
            ),
        )
    return ReviewResult(
        path=path, narrative=narrative, body=body, allowed_numbers=allowed, meta=meta
    )


def resolve_week(week: str | None = None, today: date | None = None) -> tuple[str, date, date]:
    resolved = week or current_week(today)
    start, end = week_range(resolved)
    return resolved, start, end
