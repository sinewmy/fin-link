"""Phase 3: the validation loop, anti-confirmation-bias enforcement, append-only safety."""

from __future__ import annotations

import os
import re
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finlink.domain.context import build_context
from finlink.domain.horizon import horizon_end, is_expired
from finlink.domain.pnl import Lot, Position
from finlink.domain.portfolio import value_positions
from finlink.domain.relevance import overlap_score, prefilter, published_after
from finlink.domain.risk import Alert, Rule, RuleKind, evaluate, parse_rules, sector_weights
from finlink.ingest.base import NewsItem
from finlink.io.markdown import read_document, render_document
from finlink.llm.base import LLMRunLog
from finlink.llm.client import LLMClient
from finlink.llm.drivers.echo import EchoDriver
from finlink.llm.pipelines.validate import (
    ValidateInput,
    apply_expiry,
    run_pass_a,
    run_pass_b,
    validate,
)
from finlink.llm.schemas import EvidencePass, Synthesis, Verdict

REASON = "datacenter capex keeps rising and NVIDIA keeps its share"


# --------------------------------------------------------------------------- fixtures


def news_item(
    i: int,
    ticker: str = "NVDA",
    title: str | None = None,
    day: str | None = None,
    summary: str | None = None,
) -> NewsItem:
    return NewsItem(
        ticker=ticker,
        title=title or f"{ticker} datacenter capex guidance raised again",
        url=f"https://example.com/{ticker}/{i}",
        published_at=day or f"2026-09-0{i + 1}",
        source="example",
        summary=summary or f"Hyperscaler capex guidance and datacenter revenue for {ticker}.",
    )


@pytest.fixture()
def client(tmp_path: Path) -> LLMClient:
    return LLMClient(EchoDriver(), LLMRunLog(tmp_path / "logs" / "llm_runs.jsonl"), "echo")


@pytest.fixture()
def thesis_file(tmp_path: Path) -> Path:
    """A confirmed thesis with hand-written content we later assert is untouched."""
    path = tmp_path / "theses" / "NVDA-capex.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "frontmatter": {
            "ticker": "NVDA",
            "slug": "capex",
            "status": "active",
            "created": date(2026, 9, 1),
            "horizon": "2y",
            "base_currency": "USD",
            "invalidation_conditions": ["datacenter revenue growth below 20% YoY"],
            "confidence": "medium",
            "hypotheses": [
                {
                    "id": "h1",
                    "kind": "core",
                    "statement": "datacenter capex keeps rising",
                    "observable_metric": "hyperscaler capex guidance",
                    "status": "pending",
                },
                {
                    "id": "h2",
                    "kind": "sub",
                    "parent": "h1",
                    "statement": "NVIDIA keeps share",
                    "observable_metric": "NVDA datacenter revenue YoY",
                    "status": "pending",
                },
            ],
        },
        "body": (
            "## Thesis\n\n"
            "My own words, plus a hand-written note I care about: keep an eye on TSMC.\n\n"
            "## Hypotheses\n"
        ),
    }
    from finlink.io.markdown import Doc

    path.write_text(render_document(Doc(**doc, path=path)), encoding="utf-8")
    return path


def make_input(tmp_path: Path, **kw: object) -> ValidateInput:
    kwargs = {
        "thesis_path": tmp_path / "theses" / "NVDA-capex.md",
        "ticker": "NVDA",
        "hypotheses": [
            {
                "id": "h1",
                "kind": "core",
                "statement": "datacenter capex keeps rising",
                "observable_metric": "hyperscaler capex guidance datacenter revenue",
            },
            {
                "id": "h2",
                "kind": "sub",
                "statement": "NVIDIA keeps share",
                "observable_metric": "NVDA datacenter revenue YoY",
            },
        ],
        "created": date(2026, 9, 1),
        "horizon": "2y",
        "news": [news_item(i) for i in range(4)],
        "as_of": date(2026, 9, 6),
    }
    kwargs.update(kw)
    return ValidateInput(**kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------- relevance prefilter


def test_prefilter_requires_same_ticker():
    assert not prefilter(
        [news_item(0, ticker="AAPL")],
        ticker="NVDA",
        since="2026-09-01",
        observable_metric="capex guidance",
    )


def test_prefilter_requires_published_after_thesis_creation():
    old = news_item(0, day="2026-01-01")
    assert not prefilter(
        [old], ticker="NVDA", since="2026-09-01", observable_metric="hyperscaler capex guidance"
    )


def test_prefilter_requires_token_overlap():
    unrelated = news_item(0, title="Board appoints a new director", summary="Governance update.")
    assert not prefilter(
        [unrelated],
        ticker="NVDA",
        since="2026-09-01",
        observable_metric="hyperscaler capex guidance datacenter revenue",
    )


def test_prefilter_keeps_relevant_and_sorts_by_score():
    out = prefilter(
        [news_item(i) for i in range(3)],
        ticker="NVDA",
        since="2026-09-01",
        observable_metric="datacenter capex guidance",
    )
    assert len(out) == 3
    assert all(c.score > 0 for c in out)
    assert [c.score for c in out] == sorted((c.score for c in out), reverse=True)


def test_published_after_compares_date_prefix_only():
    assert published_after(news_item(0, day="2026-09-01T13:00:00Z"), "2026-09-01")


def test_overlap_score_is_zero_when_no_shared_tokens():
    item = news_item(0, title="zzz qqq", summary="nothing relevant here")
    assert overlap_score(item, "hyperscaler capex") == 0.0


# ------------------------------------------------------------------------- horizon


def test_horizon_end_months_and_years():
    assert horizon_end(date(2026, 1, 31), "1m") == date(2026, 2, 28)  # clamped
    assert horizon_end(date(2026, 1, 1), "2y") == date(2028, 1, 1)
    assert horizon_end(date(2026, 1, 1), "30d") == date(2026, 1, 31)


def test_horizon_end_returns_none_for_unparseable():
    assert horizon_end(date(2026, 1, 1), "long-term") is None
    assert horizon_end(date(2026, 1, 1), "") is None


def test_expired_only_after_the_horizon_passes():
    assert not is_expired(date(2024, 1, 1), "2y", date(2025, 1, 1))
    assert is_expired(date(2024, 1, 1), "2y", date(2026, 6, 1))


# ------------------------------------------------------------------------- risk engine


def _view():
    """NVDA 10@200 = 2000, AAPL 10@50 = 500, cash 500 -> total 3000 (NVDA 66.67%)."""
    pos = Position(ticker="NVDA", currency="USD")
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), currency="USD"))
    other = Position(ticker="AAPL", currency="USD")
    other.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), currency="USD"))
    prices = {"NVDA": Decimal("200"), "AAPL": Decimal("50")}
    return value_positions({"NVDA": pos, "AAPL": other}, prices, {"USD": Decimal("500")})


def test_max_position_weight_breach():
    view = _view()
    # NVDA 66.67%, AAPL 16.67% — only NVDA clears a 20% limit.
    rules = [Rule(id="r1", kind=RuleKind.MAX_POSITION_WEIGHT, limit=Decimal("20"))]
    alerts = evaluate(view, rules)
    assert len(alerts) == 1
    assert alerts[0].scope == "NVDA"
    assert "BREACH" not in alerts[0].message  # message is prose; render adds BREACHED
    assert alerts[0].value_pct > alerts[0].limit_pct


def test_no_alert_when_inside_limits():
    view = _view()  # largest holding is 66.67%
    rules = [Rule(id="r1", kind=RuleKind.MAX_POSITION_WEIGHT, limit=Decimal("80"))]
    assert evaluate(view, rules) == []


def test_sector_and_cash_rules():
    view = _view()
    rules = [
        Rule(id="s1", kind=RuleKind.MAX_SECTOR_WEIGHT, limit=Decimal("20"), scope="Info Tech"),
        Rule(id="c1", kind=RuleKind.MIN_CASH_PCT, limit=Decimal("50")),  # cash is 16.67%
    ]
    alerts = evaluate(view, rules, sectors={"NVDA": "Info Tech", "AAPL": "Info Tech"})
    kinds = {a.rule_id for a in alerts}
    assert kinds == {"s1", "c1"}


def test_drawdown_rule_uses_absolute_value():
    view = _view()
    rules = [Rule(id="d1", kind=RuleKind.MAX_DRAWDOWN_PCT, limit=Decimal("20"))]
    alerts = evaluate(view, rules, drawdowns={"NVDA": Decimal("-35.5")})
    assert len(alerts) == 1
    assert alerts[0].value_pct == Decimal("35.5")


def test_malformed_rule_fails_loud():
    with pytest.raises(ValueError, match="unknown kind"):
        Rule.parse({"id": "x", "kind": "vibes", "limit": 10})
    with pytest.raises(ValueError, match="missing limit"):
        Rule.parse({"id": "x", "kind": "max_position_weight"})


def test_parse_rules_rejects_non_mapping():
    with pytest.raises(ValueError, match="must be a mapping"):
        parse_rules(["not-a-rule"])


def test_sector_weights_bucket_unmapped_tickers():
    view = _view()
    # 2500 of 3000 total is in positions; cash is not assigned a sector.
    unclassified = sector_weights(view, {})["Unclassified"]
    assert unclassified == pytest.approx(Decimal("83.3333"), abs=Decimal("0.001"))


# ------------------------------------------------------------- portfolio context 4.4


def test_context_renders_breach_and_lists_allowed_numbers():
    view = _view()
    rules = [Rule(id="r1", kind=RuleKind.MAX_POSITION_WEIGHT, limit=Decimal("15"))]
    alerts = evaluate(view, rules)
    ctx = build_context(view, "NVDA", alerts=alerts)
    rendered = ctx.render()
    assert "BREACHED" in rendered
    assert "weight" in rendered
    # the exact figures the model may quote must be in the allow-list
    assert "66.67" in ctx.allowed_numbers
    assert "15" in ctx.allowed_numbers  # the limit the model may quote


def test_context_handles_unpriced_ticker_without_inventing_zero():
    view = _view()
    ctx = build_context(view, "TSLA")
    assert ctx.position is None
    assert ctx.unpriced
    assert "not in priced holdings" in ctx.render()


def test_position_note_with_foreign_number_is_rejected():
    view = _view()
    ctx = build_context(view, "NVDA")
    synth = Synthesis(
        verdict=Verdict.CHALLENGED,
        confidence="medium",
        rationale="both sides found",
        position_note="this name is 22.3% of the portfolio against a 15% limit",
        uncertainty="limited sources",
        supporting_count=1,
        contrary_count=1,
    )
    with pytest.raises(ValueError, match="absent from the computed portfolio context"):
        synth.check_position_note_numbers(ctx.allowed_numbers)


def test_position_note_with_only_context_numbers_passes():
    view = _view()
    ctx = build_context(view, "NVDA")
    weight = ctx.render().split("weight ")[1].split("%")[0]
    synth = Synthesis(
        verdict=Verdict.CHALLENGED,
        confidence="medium",
        rationale="both sides found",
        position_note=f"this name is {weight}% of the portfolio, which is the binding constraint",
        uncertainty="limited sources",
        supporting_count=1,
        contrary_count=1,
    )
    synth.check_position_note_numbers(ctx.allowed_numbers)  # must not raise


# ------------------------------------------------------------ schema bias guards


def test_still_valid_with_no_contrary_evidence_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="still_valid"):
        Synthesis(
            verdict=Verdict.STILL_VALID,
            confidence="high",
            rationale="looks good",
            position_note="fine",
            uncertainty="none",
            supporting_count=3,
            contrary_count=0,
        )


def test_evidence_pass_with_no_items_requires_a_reason():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="not_found_reason"):
        EvidencePass(direction="supporting", items=[])


def test_evidence_item_requires_resolvable_url():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="http"):
        EvidencePass.model_validate(
            {
                "direction": "supporting",
                "items": [
                    {
                        "hypothesis_id": "h1",
                        "claim": "x",
                        "url": "example.com/no-scheme",
                        "published_at": "2026-09-01",
                    }
                ],
            }
        )


# ------------------------------------------------------- two independent passes


def test_pass_b_never_receives_pass_a_output(tmp_path: Path, client: LLMClient):
    """The core isolation guarantee: Pass B's prompt contains no Pass A output."""
    inp = make_input(tmp_path)
    seen: list[str] = []

    class Spy(EchoDriver):
        def complete(self, *, system, user, schema, model=None):  # type: ignore[no-untyped-def]
            seen.append(f"{system}\n{user}")
            return super().complete(system=system, user=user, schema=schema, model=model)

    spy_client = LLMClient(Spy(), LLMRunLog(tmp_path / "spy.jsonl"), "echo")
    from finlink.domain.relevance import metric_of
    from finlink.domain.relevance import prefilter as pf

    cands = pf(
        inp.news,
        ticker=inp.ticker,
        since=inp.created.isoformat(),
        observable_metric=metric_of(inp.hypotheses),
    )
    a, _ = run_pass_a(inp, cands, spy_client, "echo")
    b, _ = run_pass_b(inp, cands, spy_client, "echo")

    assert a.direction == "supporting"
    assert b.direction == "contrary"
    prompt_b = seen[-1]
    for item in a.items:
        assert item.claim not in prompt_b, "Pass A's conclusion leaked into Pass B"


def test_passes_differ_in_output_not_just_label(tmp_path: Path, client: LLMClient):
    inp = make_input(tmp_path)
    from finlink.domain.relevance import metric_of
    from finlink.domain.relevance import prefilter as pf

    cands = pf(
        inp.news,
        ticker=inp.ticker,
        since=inp.created.isoformat(),
        observable_metric=metric_of(inp.hypotheses),
    )
    a, _ = run_pass_a(inp, cands, client, "echo")
    b, _ = run_pass_b(inp, cands, client, "echo")
    assert {i.claim for i in a.items} != {i.claim for i in b.items}


# ------------------------------------------------------------ end-to-end + safety


def test_validate_appends_both_sections(tmp_path: Path, client: LLMClient, thesis_file: Path):
    inp = make_input(tmp_path, thesis_path=thesis_file)
    result = validate(inp, client=client, model_a="echo", model_b="echo", model_s="echo")

    text = thesis_file.read_text(encoding="utf-8")
    assert "### Supporting" in text
    assert "### Contrary" in text
    assert "### Uncertainty" in text
    assert "### Portfolio context" in text
    assert result.synthesis.uncertainty.strip()
    assert re.search(r"## Validation — \d{4}-\d{2}-\d{2}", text)


def test_handwritten_content_is_byte_identical_after_validation(
    tmp_path: Path, client: LLMClient, thesis_file: Path
):
    """The append-only guarantee — the reason this design exists.

    Frontmatter status/confidence/last_validated are *supposed* to change; every
    byte of the body below the frontmatter must survive untouched.
    """
    before_body = read_document(thesis_file).body
    inp = make_input(tmp_path, thesis_path=thesis_file)
    validate(inp, client=client, model_a="echo", model_b="echo", model_s="echo")
    after_body = read_document(thesis_file).body
    assert after_body.startswith(before_body), "existing body was modified, not appended to"
    assert "keep an eye on TSMC" in after_body


def test_validate_updates_status_and_last_validated(
    tmp_path: Path, client: LLMClient, thesis_file: Path
):
    inp = make_input(tmp_path, thesis_path=thesis_file)
    result = validate(inp, client=client, model_a="echo", model_b="echo", model_s="echo")
    fm = read_document(thesis_file).frontmatter
    assert fm["last_validated"] == "2026-09-06"
    assert fm["status"] in ("active", "challenged", "invalidated")
    assert fm["confidence"] == result.synthesis.confidence.value


def test_no_evidence_yields_undetermined_not_still_valid(tmp_path: Path, client: LLMClient):
    inp = make_input(tmp_path, news=[])
    result = validate(
        inp, client=client, model_a="echo", model_b="echo", model_s="echo", write=False
    )
    assert result.synthesis.verdict is Verdict.UNDETERMINED
    assert result.contrary.items == []
    assert result.contrary.not_found_reason


def test_horizon_expiry_marks_hypotheses_expired(tmp_path: Path, thesis_file: Path):
    inp = make_input(
        tmp_path,
        thesis_path=thesis_file,
        horizon="1m",
        created=date(2020, 1, 1),
        as_of=date(2026, 9, 6),
    )
    assert inp.hypotheses
    apply_expiry(thesis_file, [str(h["id"]) for h in inp.hypotheses])
    fm = read_document(thesis_file).frontmatter
    assert all(h["status"] == "expired" for h in fm["hypotheses"])


def test_expiry_does_not_touch_the_body(tmp_path: Path, thesis_file: Path):
    before = read_document(thesis_file).body
    apply_expiry(thesis_file, ["h1"])
    assert read_document(thesis_file).body == before
    hyps = read_document(thesis_file).frontmatter["hypotheses"]
    statuses = {h["id"]: h["status"] for h in hyps}
    assert statuses == {"h1": "expired", "h2": "pending"}


def test_evidence_items_carry_source_url_and_date(tmp_path: Path, client: LLMClient):
    inp = make_input(tmp_path)
    result = validate(
        inp, client=client, model_a="echo", model_b="echo", model_s="echo", write=False
    )
    for item in [*result.supporting.items, *result.contrary.items]:
        assert item.url.startswith("https://")
        assert item.published_at
        assert item.claim


def test_validate_is_idempotent_on_frontmatter_not_on_sections(
    tmp_path: Path, client: LLMClient, thesis_file: Path
):
    """Re-running appends a second section rather than replacing the first."""
    inp = make_input(tmp_path, thesis_path=thesis_file)
    validate(inp, client=client, model_a="echo", model_b="echo", model_s="echo")
    first = thesis_file.read_text(encoding="utf-8")
    validate(inp, client=client, model_a="echo", model_b="echo", model_s="echo")
    second = thesis_file.read_text(encoding="utf-8")
    assert first in second
    assert second.count("## Validation —") == 2


def test_alert_type_is_immutable():
    """Alerts are frozen: no code path may soften or close one after the fact."""
    a = Alert(
        rule_id="r",
        kind=RuleKind.MAX_POSITION_WEIGHT,
        scope="X",
        value_pct=Decimal("1"),
        limit_pct=Decimal("2"),
        message="m",
    )
    with pytest.raises(FrozenInstanceError):
        a.message = "tampered"  # type: ignore[misc]


# ------------------------------------------------------------------ CLI surface


def test_cli_validate_end_to_end_on_echo_driver(tmp_path: Path):
    """`finlink validate` runs offline, appends, and reports both halves."""
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    runner = CliRunner()
    assert runner.invoke(main, ["init", str(root)]).exit_code == 0
    # find_root() walks up from the CWD, so run inside the scratch workspace rather
    # than the repo hosting these tests.
    cwd = Path.cwd()
    os.chdir(root)
    try:
        _run_cli_validate_flow(runner, root)
    finally:
        os.chdir(cwd)


def _run_cli_validate_flow(runner: object, root: Path) -> None:
    from finlink.cli import main

    trade = runner.invoke(  # noqa: F841
        main,
        [
            "record-trade",
            "--ticker",
            "NVDA",
            "--side",
            "buy",
            "--quantity",
            "20",
            "--price",
            "175",
            "--reason",
            "datacenter capex keeps rising and NVIDIA keeps share",
            "--horizon",
            "2y",
            "--driver",
            "echo",
            "--no-commit",
        ],
        catch_exceptions=False,
    )
    assert trade.exit_code == 0, trade.output
    thesis = next((root / "theses").glob("*.md"))
    assert runner.invoke(main, ["confirm", str(thesis), "--no-commit"]).exit_code == 0

    validated = runner.invoke(
        main, ["validate", "--driver", "echo", "--no-commit"], catch_exceptions=False
    )
    assert validated.exit_code == 0, validated.output
    assert "### Supporting" in validated.output
    assert "### Contrary" in validated.output
    assert "### Uncertainty" in validated.output
    text = thesis.read_text(encoding="utf-8")
    assert "## Validation —" in text
    assert "datacenter capex keeps rising and NVIDIA keeps share" in text


def test_cli_status_lists_theses(tmp_path: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    runner = CliRunner()
    assert runner.invoke(main, ["init", str(root)]).exit_code == 0
    cwd = Path.cwd()
    os.chdir(root)
    try:
        out = runner.invoke(main, ["status"], catch_exceptions=False)
    finally:
        os.chdir(cwd)
    assert out.exit_code == 0
    assert "no theses yet" in out.output


def test_validate_skips_draft_theses(tmp_path: Path):
    """A draft must not be validated — the user has not confirmed the reasoning yet."""
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    runner = CliRunner()
    assert runner.invoke(main, ["init", str(root)]).exit_code == 0
    cwd = Path.cwd()
    os.chdir(root)
    try:
        runner.invoke(
            main,
            [
                "record-trade",
                "--ticker",
                "NVDA",
                "--side",
                "buy",
                "--quantity",
                "1",
                "--price",
                "10",
                "--reason",
                "draft only",
                "--driver",
                "echo",
                "--no-commit",
            ],
            catch_exceptions=False,
        )
        out = runner.invoke(main, ["validate", "--driver", "echo", "--no-commit"])
    finally:
        os.chdir(cwd)
    assert out.exit_code != 0
    assert "no active theses" in out.output


def test_cli_tests_do_not_pollute_the_real_workspace(tmp_path: Path):
    """Guard: init must target the scratch dir, not the repo hosting the tests."""
    from click.testing import CliRunner

    from finlink.cli import main
    from finlink.workspace import find_root

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    assert (root / ".finlink").exists()
    assert find_root(root) == root
