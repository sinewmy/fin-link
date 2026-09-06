"""Pipeline P1 — thesis decomposition.

Input:  the user's reason, in their own words.
Output: a thesis file with status: draft (DRAFT until the user confirms).
The model delivers NO investment insight here — it only structures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finlink.io.markdown import Doc, render_document, write_atomic
from finlink.llm.base import LLMMetadata
from finlink.llm.client import LLMClient
from finlink.llm.schemas import ThesisDecomposition


@dataclass
class DecomposeInput:
    ticker: str
    reason: str
    horizon: str = ""
    slug: str = ""
    as_of: date | None = None


@dataclass
class DecomposeResult:
    path: Path
    decomposition: ThesisDecomposition
    meta: LLMMetadata
    frontmatter: dict[str, object]


def slugify(text: str, max_len: int = 40) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "-".join(slug.split("-")[:6])[:max_len].strip("-") or "thesis"


def build_frontmatter(
    inp: DecomposeInput, dec: ThesisDecomposition, as_of: date
) -> dict[str, object]:
    return {
        "ticker": inp.ticker,
        "slug": inp.slug or slugify(inp.reason),
        "status": "draft",  # never auto-activate; the user must confirm
        "created": as_of.isoformat(),
        "horizon": inp.horizon or dec.horizon_suggestion,
        "base_currency": "USD",
        "invalidation_conditions": dec.invalidation_conditions,
        "confidence": "medium",
        "hypotheses": [
            {
                "id": h.id,
                "kind": h.kind,
                "statement": h.statement,
                "observable_metric": h.observable_metric,
                "status": "pending",
                **({"parent": h.parent} if h.parent else {}),
            }
            for h in dec.hypotheses
        ],
    }


def render_thesis_body(inp: DecomposeInput, dec: ThesisDecomposition) -> str:
    lines = [
        "## Thesis",
        "",
        inp.reason.strip(),  # verbatim — never paraphrased
        "",
        "## Hypotheses",
        "",
    ]
    for h in dec.hypotheses:
        prefix = "Core" if h.kind == "core" else f"Sub (of {h.parent})"
        lines.append(f"- **{h.id}** [{prefix}] {h.statement}")
        lines.append(f"  - Observable: {h.observable_metric}")
    lines += ["", "## Invalidation conditions", ""]
    lines += [f"- {c}" for c in dec.invalidation_conditions]
    lines += ["", "_Status is `draft` until you confirm the decomposition above._", ""]
    return "\n".join(lines)


def run(
    inp: DecomposeInput,
    *,
    client: LLMClient,
    theses_dir: Path,
    model: str | None = None,
) -> DecomposeResult:
    dec, meta = client.run(
        pipeline="P1_decompose",
        prompt_name="decompose_thesis_v1",
        schema=ThesisDecomposition,
        variables={
            "ticker": inp.ticker,
            "reason": inp.reason,
            "horizon": inp.horizon or "not specified",
            "user_prompt": (
                f"TICKER: {inp.ticker}\n"
                f"HORIZON: {inp.horizon or 'not specified'}\n"
                f"REASON: {inp.reason}\n"
            ),
        },
        model=model,
    )
    as_of = inp.as_of or date.today()
    fm = build_frontmatter(inp, dec, as_of)
    slug = fm["slug"]
    path = theses_dir / f"{inp.ticker}-{slug}.md"
    if path.exists():
        raise FileExistsError(f"thesis file already exists: {path}")

    body = render_thesis_body(inp, dec)
    write_atomic(path, render_document(Doc(frontmatter=fm, body=body, path=path)))
    return DecomposeResult(path=path, decomposition=dec, meta=meta, frontmatter=fm)
