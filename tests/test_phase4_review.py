"""Phase 4: risk rails, the alert ledger, and the two-half weekly review."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finlink.domain.alerts import (
    STATUS_ACKNOWLEDGED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    acknowledge,
    acknowledged_alerts,
    open_alerts,
    reconcile,
)
from finlink.domain.drift import (
    Pattern,
    current_week,
    detect_patterns,
    diff,
    round_trips,
    snapshot,
    week_range,
)
from finlink.domain.pnl import Lot, Position
from finlink.domain.portfolio import value_positions
from finlink.domain.risk import Alert, Rule, RuleKind, evaluate, parse_rules
from finlink.domain.usage import compute
from finlink.models import LedgerRow, Side, ThesisFrontmatter
from finlink.workspace import Config

# ------------------------------------------------------------------------ fixtures


def row(
    ticker: str,
    side: str,
    qty: str,
    price: str,
    day: date,
    reason: str = "",
    ccy: str = "USD",
    slug: str | None = None,
) -> LedgerRow:
    return LedgerRow(
        date=day,
        ticker=ticker,
        side=Side(side),
        quantity=Decimal(qty),
        price=Decimal(price),
        currency=ccy,
        fees=Decimal("0"),
        reason=reason,
        thesis_slug=slug,
        fx_rate_usd_at_trade=None,
    )


def alert(rule_id: str = "r1", scope: str = "NVDA", value: str = "22", limit: str = "15") -> Alert:
    return Alert(
        rule_id=rule_id,
        kind=RuleKind.MAX_POSITION_WEIGHT,
        scope=scope,
        value_pct=Decimal(value),
        limit_pct=Decimal(limit),
        message=f"{scope} over limit",
    )


def concentrated_view():
    """NVDA 10@200 = 2000, AAPL 10@50 = 500, cash 500 -> total 3000."""
    nvda = Position(ticker="NVDA", currency="USD")
    nvda.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), currency="USD"))
    aapl = Position(ticker="AAPL", currency="USD")
    aapl.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), currency="USD"))
    return value_positions(
        {"NVDA": nvda, "AAPL": aapl},
        {"NVDA": Decimal("200"), "AAPL": Decimal("50")},
        {"USD": Decimal("500")},
    )


# ------------------------------------------------------------------- risk + alerts


def test_over_concentrated_portfolio_triggers_alerts():
    view = concentrated_view()
    rules = [Rule(id="concentration", kind=RuleKind.MAX_POSITION_WEIGHT, limit=Decimal("15"))]
    alerts = evaluate(view, rules)
    assert {a.scope for a in alerts} == {"NVDA", "AAPL"}
    assert all(a.value_pct > a.limit_pct for a in alerts)


def test_reconcile_keeps_raised_date_and_acknowledgement_across_drift():
    """A drifting weight must not reset the alert or wipe an acknowledgement."""
    day1 = date(2026, 9, 1)
    first = reconcile([], [alert(value="22")], day1)
    assert len(first) == 1
    assert first[0].raised == day1
    assert first[0].status == STATUS_OPEN

    day2 = date(2026, 9, 8)
    second = reconcile(acknowledge(first, first[0].key, day2), [alert(value="31")], day2)
    assert len(second) == 1
    assert second[0].raised == day1, "raised date must survive a re-run"
    assert second[0].status == STATUS_ACKNOWLEDGED
    assert second[0].observed == Decimal("31"), "the fresh value should be shown"
    assert second[0].acked == day2


def test_alert_that_stops_breaching_is_resolved_not_deleted():
    day1 = date(2026, 9, 1)
    first = reconcile([], [alert()], day1)
    later = reconcile(first, [], date(2026, 9, 8))
    assert len(later) == 1
    assert later[0].status == STATUS_RESOLVED
    assert open_alerts(later) == []


def test_acknowledged_alert_is_never_auto_resolved():
    """Acknowledgement outranks resolution: only the user closes an alert."""
    day1 = date(2026, 9, 1)
    rec = acknowledge(reconcile([], [alert()], day1), "r1:NVDA", day1)
    later = reconcile(rec, [], date(2026, 9, 8))
    assert later[0].status == STATUS_ACKNOWLEDGED
    assert acknowledged_alerts(later)


def test_new_condition_is_added_as_open():
    day1 = date(2026, 9, 1)
    first = reconcile([], [alert()], day1)
    second = reconcile(first, [alert(), alert(scope="AAPL")], date(2026, 9, 8))
    assert len(second) == 2
    assert {r.status for r in second} == {STATUS_OPEN}


def test_acknowledge_unknown_key_raises():
    with pytest.raises(KeyError):
        acknowledge([], "nope:NOPE", date(2026, 9, 1))


def test_no_llm_path_can_suppress_an_alert():
    """The guarantee, stated as a test: Alert is frozen and evaluation is pure."""
    from dataclasses import FrozenInstanceError

    a = alert()
    with pytest.raises(FrozenInstanceError):
        a.message = "softened"  # type: ignore[misc]
    # and evaluation depends only on (view, rules) — no narrative input exists
    view = concentrated_view()
    rules = [Rule(id="c", kind=RuleKind.MAX_POSITION_WEIGHT, limit=Decimal("15"))]
    assert evaluate(view, rules) == evaluate(view, rules)


# --------------------------------------------------------------------------- drift


def test_snapshot_weights_sum_to_positions_plus_cash():
    snap = snapshot(
        {"NVDA": _pos("NVDA", "10"), "AAPL": _pos("AAPL", "10")},
        {"NVDA": Decimal("200"), "AAPL": Decimal("50")},
        {"USD": Decimal("500")},
        {},
        date(2026, 9, 1),
    )
    assert snap.total_usd == Decimal("3000")
    assert snap.by_ticker["NVDA"] > snap.by_ticker["AAPL"]
    assert snap.cash_pct > 0


def _pos(ticker: str, qty: str) -> Position:
    p = Position(ticker=ticker, currency="USD")
    p.add_lot(Lot(quantity=Decimal(qty), unit_cost=Decimal("100"), currency="USD"))
    return p


def test_drift_reports_change_per_position():
    start = snapshot(
        {"NVDA": _pos("NVDA", "10")},
        {"NVDA": Decimal("100")},
        {"USD": Decimal("900")},
        {},
        date(2026, 9, 1),
    )
    end = snapshot(
        {"NVDA": _pos("NVDA", "10")},
        {"NVDA": Decimal("150")},
        {"USD": Decimal("900")},
        {},
        date(2026, 9, 8),
    )
    drifts = diff(start, end)
    assert drifts["position"]
    assert drifts["position"][0].change_pct > 0
    assert drifts["position"][0].end_pct > drifts["position"][0].start_pct


def test_weight_drift_is_measurable_when_prices_move():
    """Exit criterion: the portfolio half shows measurable weight drift."""
    start = snapshot(
        {"NVDA": _pos("NVDA", "10"), "AAPL": _pos("AAPL", "10")},
        {"NVDA": Decimal("100"), "AAPL": Decimal("100")},
        {"USD": Decimal("1000")},
        {},
        date(2026, 9, 1),
    )
    end = snapshot(
        {"NVDA": _pos("NVDA", "10"), "AAPL": _pos("AAPL", "10")},
        {"NVDA": Decimal("200"), "AAPL": Decimal("100")},
        {"USD": Decimal("1000")},
        {},
        date(2026, 9, 8),
    )
    by_name = {d.name: d.change_pct for d in diff(start, end)["position"]}
    assert by_name["NVDA"] > 0
    assert by_name["AAPL"] < 0  # fell as a share even though the price held


# ---------------------------------------------------------------- behavioural


def test_quick_sell_pattern_is_detected_and_countable():
    """Exit criterion: >=1 concrete behavioural pattern from seeded history."""
    d0 = date(2026, 1, 1)
    ledger = [
        row("AAA", "buy", "10", "100", d0, "cheap"),
        row("AAA", "sell", "10", "110", d0 + timedelta(days=5), "took profit"),
        row("BBB", "buy", "10", "100", d0 + timedelta(days=1), "cheap"),
        row("BBB", "sell", "10", "110", d0 + timedelta(days=9), "took profit"),
        row("CCC", "buy", "10", "100", d0 + timedelta(days=2), "cheap"),
        row("CCC", "sell", "10", "110", d0 + timedelta(days=12), "took profit"),
    ]
    patterns = detect_patterns(ledger, as_of=d0 + timedelta(days=60))
    names = [p.name for p in patterns]
    assert any("within" in n for n in names), names
    quick = next(p for p in patterns if "within" in p.name)
    assert "3 of 3" in quick.detail
    assert len(quick.evidence) == 3


def test_losers_closed_faster_than_winners():
    d0 = date(2026, 1, 1)
    ledger = [
        row("WIN", "buy", "10", "100", d0),
        row("WIN", "sell", "10", "150", d0 + timedelta(days=90)),
        row("LOSE", "buy", "10", "100", d0),
        row("LOSE", "sell", "10", "50", d0 + timedelta(days=10)),
    ]
    names = [p.name for p in detect_patterns(ledger, as_of=d0 + timedelta(days=120))]
    assert any("losses closed faster" in n for n in names), names


def test_buys_without_reason_are_flagged():
    d0 = date(2026, 1, 1)
    ledger = [row("X", "buy", "1", "10", d0), row("Y", "buy", "1", "10", d0)]
    names = [p.name for p in detect_patterns(ledger, as_of=d0)]
    assert any("without a reason" in n for n in names)


def test_round_trips_match_fifo():
    d0 = date(2026, 1, 1)
    trips = round_trips(
        [
            row("A", "buy", "10", "100", d0),
            row("A", "buy", "10", "120", d0 + timedelta(days=1)),
            row("A", "sell", "15", "130", d0 + timedelta(days=10)),
        ]
    )
    assert len(trips) == 2
    assert trips[0].bought == d0
    assert trips[1].bought == d0 + timedelta(days=1)
    assert all(t.days_held == 10 or t.days_held == 9 for t in trips)


def test_no_patterns_on_empty_history():
    assert detect_patterns([], as_of=date(2026, 1, 1)) == []


# -------------------------------------------------------------------------- §16


def test_usage_metrics_computed():
    d0 = date(2026, 1, 1)
    ledger = [
        row("A", "buy", "10", "100", d0, "good reason", slug="a"),
        row("B", "buy", "10", "100", d0, ""),
    ]
    thesis = ThesisFrontmatter(
        ticker="A",
        slug="a",
        status="active",
        created=d0,
        invalidation_conditions=["x"],
        last_validated=d0,
    )
    m = compute(ledger, [(Path("a.md"), thesis)])
    assert m.transactions == 2
    assert m.with_reason == 1
    assert m.pct_with_reason == Decimal("50.0")
    assert m.theses_total == 1
    assert m.pct_validated == Decimal("100.0")
    assert m.pct_with_invalidation == Decimal("100.0")
    assert "%" in m.render()


def test_usage_metrics_handle_empty_workspace():
    m = compute([], [])
    assert m.transactions == 0
    assert m.pct_with_reason == Decimal("0")


# ------------------------------------------------------------------------ weeks


def test_week_range_monday_to_sunday():
    start, end = week_range("2026-W37")
    assert start.weekday() == 0
    assert end.weekday() == 6
    assert (end - start).days == 6


def test_bad_week_raises():
    with pytest.raises(ValueError, match="invalid week"):
        week_range("2026-37")


def test_current_week_format():
    assert current_week(date(2026, 9, 7)) == "2026-W37"


# --------------------------------------------------------------- P4 narrative


def test_review_narrative_rejects_invented_numbers():
    from finlink.llm.schemas import ReviewNarrative

    n = ReviewNarrative(
        individual="I bought more than I planned, at 47.3% of the portfolio",
        portfolio="concentration rose",
        uncertainty="plenty",
    )
    with pytest.raises(ValueError, match="did not compute"):
        n.check_numbers({"15", "22"})


def test_review_narrative_thousands_separator_atomic():
    """225,005.06 is one number; 'fell from 225,600.83' is an invented baseline.

    Regression: the tokenizer must not split the decimal fraction into '06'/'83'
    fragments, and a prior-week total absent from facts must still be rejected.
    """
    from finlink.llm.schemas import ReviewNarrative

    ok = ReviewNarrative(
        individual="no trades",
        portfolio="total is 225,005.06, cash 35.28%",
        uncertainty="none",
    )
    ok.check_numbers({"225005.06", "35.28"})  # fraction atomic, passes

    bad = ReviewNarrative(
        individual="no trades",
        portfolio="total fell from 225,600.83 to 225,005.06",
        uncertainty="none",
    )
    with pytest.raises(ValueError, match="did not compute"):
        bad.check_numbers({"225005.06", "35.28"})


def test_review_narrative_accepts_rounding_rejects_unit_change():
    """Value-based audit: $225005 is a rounding of $225005.06 (allowed);
    0.15 is a unit change from the 15% limit (rejected)."""
    from finlink.llm.schemas import ReviewNarrative

    ok = ReviewNarrative(
        individual="total is roughly $225005",
        portfolio="no drift",
        uncertainty="none",
    )
    ok.check_numbers({"225005.06", "15"})  # rounding passes

    bad = ReviewNarrative(
        individual="limit is 0.15",
        portfolio="no drift",
        uncertainty="none",
    )
    with pytest.raises(ValueError, match="did not compute"):
        bad.check_numbers({"225005.06", "15"})  # fraction != percent


def test_review_narrative_allows_ticker_digits_in_facts():
    """Ticker digits (00700.HK, 01810.HK) come from conflict facts, not math.

    Regression: the allow-list is built from render_facts(), so ticker numbers
    must pass the audit — they are names, not computed figures.
    """
    from finlink.llm.schemas import ReviewNarrative

    n = ReviewNarrative(
        individual="Reviewing 00700.HK and 01810.HK positions",
        portfolio="concentration breach noted for 00700.HK at 17.37% vs 15.00%",
        uncertainty="none additional",
    )
    # tickers are stripped as names before the numeric audit
    n.check_numbers({"17.37", "15"}, tickers={"00700.HK", "01810.HK"})


def test_review_narrative_allows_supplied_numbers():
    from finlink.llm.schemas import ReviewNarrative

    n = ReviewNarrative(
        individual="I added to a name already at 22.3%",
        portfolio="weight moved from 15% to 22.3%",
        uncertainty="unrecorded reasoning",
    )
    n.check_numbers({"22.3", "15"})


def test_review_renders_both_halves_echo(tmp_path: Path):
    from finlink.llm.base import LLMRunLog
    from finlink.llm.client import LLMClient
    from finlink.llm.drivers.echo import EchoDriver
    from finlink.llm.pipelines.review import ReviewInput
    from finlink.llm.pipelines.review import run as review_run

    d0 = date(2026, 9, 1)
    start = snapshot(
        {"NVDA": _pos("NVDA", "10")}, {"NVDA": Decimal("100")}, {"USD": Decimal("900")}, {}, d0
    )
    end = snapshot(
        {"NVDA": _pos("NVDA", "10")},
        {"NVDA": Decimal("200")},
        {"USD": Decimal("900")},
        {},
        d0 + timedelta(days=6),
    )
    inp = ReviewInput(
        week="2026-W36",
        start=d0,
        end=d0 + timedelta(days=6),
        period_trades=[row("NVDA", "buy", "10", "100", d0, "capex rising")],
        start_snapshot=start,
        end_snapshot=end,
        alerts=[],
        conflicts=[],
        patterns=[
            Pattern(name="quick sells", detail="3 of 5 sells within 14 days", evidence=("x",))
        ],
        usage=compute([row("NVDA", "buy", "10", "100", d0, "capex rising")], []),
        portfolio_return_pct=Decimal("10"),
    )
    client = LLMClient(EchoDriver(), LLMRunLog(tmp_path / "l.jsonl"), "echo")
    result = review_run(inp, client=client, model="echo", reviews_dir=tmp_path / "reviews")

    assert "## Individual" in result.body
    assert "## Portfolio" in result.body
    assert "### Uncertainty" in result.body
    assert "Not investment advice" in result.body
    assert "Weight drift" in result.body
    assert "Behavioural patterns" in result.body
    assert "Process metrics" in result.body
    assert result.path.exists()


def test_review_flags_thesis_vs_portfolio_conflict(tmp_path: Path):
    """Exit criterion: a valid thesis on an over-limit position is surfaced."""
    from finlink.llm.base import LLMRunLog
    from finlink.llm.client import LLMClient
    from finlink.llm.drivers.echo import EchoDriver
    from finlink.llm.pipelines.review import ReviewInput
    from finlink.llm.pipelines.review import run as review_run

    d0 = date(2026, 9, 1)
    inp = ReviewInput(
        week="2026-W36",
        start=d0,
        end=d0 + timedelta(days=6),
        conflicts=[
            "NVDA (NVDA-x.md): status active on a position that breaches concentration — "
            "22.30% vs 15.00% limit."
        ],
    )
    client = LLMClient(EchoDriver(), LLMRunLog(tmp_path / "l.jsonl"), "echo")
    result = review_run(inp, client=client, model="echo", reviews_dir=tmp_path / "reviews")
    assert "Thesis-vs-portfolio conflicts" in result.body
    assert "breaches concentration" in result.body


# ------------------------------------------------------------------ CLI surface


def test_cli_risk_check_and_alerts_roundtrip(tmp_path: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    runner = CliRunner()
    runner.invoke(main, ["init", str(root)])
    _seed(root)
    cwd = Path.cwd()
    os.chdir(root)
    try:
        ingest = runner.invoke(
            main, ["ingest", "NVDA", "AAPL", "--driver", "mock"], catch_exceptions=False
        )
        assert ingest.exit_code == 0, ingest.output
        check = runner.invoke(main, ["risk-check", "--no-commit"], catch_exceptions=False)
        assert check.exit_code == 0, check.output
        assert "concentration" in check.output
        assert (root / "alerts.md").exists()

        listed = runner.invoke(main, ["alerts"], catch_exceptions=False)
        assert listed.exit_code == 0
        assert "concentration" in listed.output

        acked = runner.invoke(
            main, ["alerts", "--ack", "concentration:NVDA", "--no-commit"], catch_exceptions=False
        )
        assert acked.exit_code == 0, acked.output
        assert "acknowledged" in acked.output

        # acknowledgement must survive a fresh evaluation
        again = runner.invoke(main, ["risk-check", "--no-commit"], catch_exceptions=False)
        assert again.exit_code == 0
        assert "acknowledged" in again.output
    finally:
        os.chdir(cwd)


def test_cli_review_writes_both_halves(tmp_path: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    runner = CliRunner()
    runner.invoke(main, ["init", str(root)])
    _seed(root)
    cwd = Path.cwd()
    os.chdir(root)
    try:
        runner.invoke(main, ["ingest", "NVDA", "AAPL", "--driver", "mock"], catch_exceptions=False)
        out = runner.invoke(
            main, ["review", "--driver", "echo", "--no-commit"], catch_exceptions=False
        )
        assert out.exit_code == 0, out.output
        assert "## Individual" in out.output
        assert "## Portfolio" in out.output
        assert "### Uncertainty" in out.output
        reviews = list((root / "reviews").glob("*.md"))
        assert len(reviews) == 1
        text = reviews[0].read_text(encoding="utf-8")
        assert "## Individual" in text and "## Portfolio" in text
    finally:
        os.chdir(cwd)


def _seed(root: Path) -> None:
    """A deliberately over-concentrated two-name portfolio with cached prices."""
    (root / "portfolio" / "ledger.md").write_text(
        "| date | ticker | side | quantity | price | currency | fees | reason | "
        "thesis_slug | fx_rate_usd_at_trade |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 2026-09-01 | NVDA | buy | 10 | 100 | USD | 0 | capex rising | - | - |\n"
        "| 2026-09-01 | AAPL | buy | 10 | 100 | USD | 0 | services growth | - | - |\n",
        encoding="utf-8",
    )
    (root / "portfolio" / "cash.md").write_text(
        "| currency | amount |\n| --- | --- |\n| USD | 500 |\n", encoding="utf-8"
    )
    (root / "config" / "config.yaml").write_text(
        "base_currency: USD\n"
        "hkd_peg: '7.8'\n"
        "fx: {}\n"
        "models: {}\n"
        "risk_rules:\n"
        "  - id: concentration\n"
        "    kind: max_position_weight\n"
        "    limit: '15'\n",
        encoding="utf-8",
    )


def test_config_risk_rules_load():
    cfg = Config.load(Path(__file__).parent)
    assert isinstance(cfg.risk_rules, list)
    assert parse_rules(cfg.risk_rules) is not None
