"""Phase 5: portfolio intelligence — exposure, correlation, snapshots, static HTML."""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finlink.domain.classify import (
    classify,
    classify_all,
    currency_for,
    infer_country,
    normalise_ticker,
)
from finlink.domain.exposure import compute
from finlink.domain.exposure import render as render_exposure
from finlink.domain.pnl import Lot, Position
from finlink.domain.portfolio import value_positions
from finlink.domain.snapshots import Snapshot, SnapshotStore, series
from finlink.domain.stats import (
    align_series,
    annualised_return,
    correlation_matrix,
    pearson,
    portfolio_drawdown,
    portfolio_return_series,
    portfolio_volatility,
)
from finlink.ingest.base import PriceBar
from finlink.report.html import ChartSeries, bar_chart, line_chart
from finlink.report.html import render as render_html


def bars(
    ticker: str, closes: list[str], ccy: str = "USD", start: date = date(2026, 1, 1)
) -> list[PriceBar]:
    out = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        v = Decimal(c)
        out.append(
            PriceBar(
                ticker=ticker,
                day=d,
                open=v,
                high=v,
                low=v,
                close=v,
                adj_close=v,
                volume=1000,
                currency=ccy,
            )
        )
    return out


def multi_market_view():
    """US + HK + SEK portfolio — the case the phase exists for."""
    nvda = Position(ticker="NVDA", currency="USD")
    nvda.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), currency="USD"))
    hk = Position(ticker="0700.HK", currency="HKD")
    hk.add_lot(Lot(quantity=Decimal("100"), unit_cost=Decimal("300"), currency="HKD"))
    sek = Position(ticker="VOLV-B.ST", currency="SEK")
    sek.add_lot(Lot(quantity=Decimal("50"), unit_cost=Decimal("200"), currency="SEK"))
    prices = {"NVDA": Decimal("200"), "0700.HK": Decimal("400"), "VOLV-B.ST": Decimal("250")}
    return value_positions(
        {"NVDA": nvda, "0700.HK": hk, "VOLV-B.ST": sek},
        prices,
        {"USD": Decimal("1000"), "SEK": Decimal("1000")},
        {"SEK": Decimal("0.095")},
    )


# ------------------------------------------------------------------- classification


def test_suffix_infers_country():
    assert infer_country("0700.HK", "HKD") == "China"
    assert infer_country("VOLV-B.ST", "SEK") == "EU"
    assert infer_country("AAPL", "USD") == "US"


def test_bare_hk_code_infers_china():
    assert infer_country("00700", "HKD") == "China"


def test_country_buckets_stay_within_us_china_eu():
    """The user's chosen three-bucket scheme: no fourth label may appear."""
    seen = {
        infer_country(t, c)
        for t, c in [("0700.HK", "HKD"), ("VOLV-B.ST", "SEK"), ("AAPL", "USD"), ("00700", "HKD")]
    }
    assert seen <= {"US", "China", "EU"}


def test_normalise_adds_suffix_for_hk_codes():
    assert normalise_ticker("00700", "HKD") == "0700.HK"
    assert normalise_ticker("AAPL", "USD") == "AAPL"


def test_currency_for_derives_from_suffix():
    assert currency_for("0700.HK", "USD") == "HKD"
    assert currency_for("VOLV-B.ST", "USD") == "SEK"


def test_explicit_config_mapping_wins():
    cls = classify("NVDA", "USD", sectors={"NVDA": "Information Technology"})
    assert cls.sector == "Information Technology"
    assert cls.bucket == "growth"


def test_classify_defaults_to_unclassified_not_a_guess():
    cls = classify("ZZZZ", "USD")
    assert cls.sector == "Unclassified"
    assert not cls.classified_sector


def test_growth_defensive_buckets():
    assert classify("X", "USD", sectors={"X": "Utilities"}).bucket == "defensive"
    assert classify("Y", "USD", sectors={"Y": "Information Technology"}).bucket == "growth"
    assert classify("Z", "USD", sectors={"Z": "Industrials"}).bucket == "Cyclical"


def test_classify_all_keys_by_normalised_ticker():
    out = classify_all([("00700", "HKD")])
    assert "0700.HK" in out


# ------------------------------------------------------------------------ exposure


def test_currency_exposure_is_explicit_for_sek_and_hkd():
    """Exit criterion: SEK/HKD exposure must be explicit, not implicit."""
    report = compute(multi_market_view())
    names = {b.name for b in report.currencies}
    assert {"USD", "HKD", "SEK"} == names
    assert "SEK" in report.fx_note and "HKD" in report.fx_note
    total = sum(b.weight_pct for b in report.currencies)
    assert total == pytest.approx(Decimal("100"), abs=Decimal("0.01"))


def test_sector_and_country_exposure_render():
    report = compute(
        multi_market_view(),
        sectors={"NVDA": "Information Technology", "0700.HK": "Communication Services"},
        countries={"VOLV-B.ST": "Sweden"},
    )
    text = render_exposure(report)
    assert "Sector exposure" in text
    assert "Country exposure" in text
    assert "Currency exposure" in text
    assert "Information Technology" in text
    assert "China" in text


def test_concentration_metrics():
    report = compute(multi_market_view())
    assert Decimal("0") < report.concentration_hhi <= Decimal("1")
    assert report.effective_positions >= 1
    assert report.top_position is not None


def test_hhi_is_one_for_a_single_position():
    pos = Position(ticker="NVDA", currency="USD")
    pos.add_lot(Lot(quantity=Decimal("10"), unit_cost=Decimal("100"), currency="USD"))
    view = value_positions({"NVDA": pos}, {"NVDA": Decimal("200")}, {})
    report = compute(view)
    assert report.concentration_hhi == pytest.approx(Decimal("1"), abs=Decimal("0.001"))
    assert report.effective_positions == pytest.approx(Decimal("1"), abs=Decimal("0.01"))


def test_unclassified_positions_are_counted_not_hidden():
    report = compute(multi_market_view())
    assert report.unclassified_count == 3
    assert "no sector mapping" in render_exposure(report)


# ---------------------------------------------------------------------- statistics


def test_pearson_perfect_positive_and_negative():
    xs = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
    assert pearson(xs, [Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]) == pytest.approx(
        Decimal("1"), abs=Decimal("0.0001")
    )
    assert pearson(xs, [Decimal("4"), Decimal("3"), Decimal("2"), Decimal("1")]) == pytest.approx(
        Decimal("-1"), abs=Decimal("0.0001")
    )


def test_pearson_zero_for_a_constant_series():
    """A constant series has undefined correlation — report 0, never divide by zero."""
    assert pearson(
        [Decimal("1")] * 5, [Decimal("2"), Decimal("1"), Decimal("3"), Decimal("0"), Decimal("4")]
    ) == Decimal("0")


def test_correlation_matrix_is_symmetric_with_unit_diagonal():
    s = {
        "A": bars("A", ["100", "110", "105", "120", "115"]),
        "B": bars("B", ["50", "55", "52", "60", "58"]),
    }
    matrix = correlation_matrix(s)
    diag = [c for c in matrix if c.a == c.b]
    assert all(c.coefficient == Decimal("1") for c in diag)
    off = [c for c in matrix if c.a != c.b]
    assert len(off) == 1
    assert -1 <= off[0].coefficient <= 1
    # A and B move together here
    assert off[0].coefficient > Decimal("0.5")


def test_short_overlap_is_flagged_unreliable():
    s = {
        "A": bars("A", ["100", "110"]),
        "B": bars("B", ["50", "40"]),
    }
    off = [c for c in correlation_matrix(s) if c.a != c.b]
    assert off and not off[0].reliable


def test_correlation_distinguishes_co_moving_from_inverse():
    """Guards against the matrix collapsing to 1.00 on every pair."""
    s = {
        "A": bars("A", ["100", "110", "105", "120", "115", "130"]),
        "B": bars("B", ["50", "55", "52", "60", "58", "65"]),
        "C": bars("C", ["80", "70", "75", "60", "65", "55"]),
    }
    coef = {(c.a, c.b): c.coefficient for c in correlation_matrix(s) if c.a != c.b}
    assert coef[("A", "B")] > Decimal("0.9"), coef
    assert coef[("A", "C")] < Decimal("-0.9"), coef
    assert coef[("B", "C")] < Decimal("-0.9"), coef


def test_series_are_aligned_on_shared_dates():
    s = {
        "A": bars("A", ["100", "110", "120"], start=date(2026, 1, 1)),
        "B": bars("B", ["50", "55"], start=date(2026, 1, 2)),
    }
    tickers, aligned = align_series(s)
    assert tickers == ["A", "B"]
    assert set(aligned["A"]) == set(aligned["B"])


def test_portfolio_volatility_and_drawdown():
    rets = [Decimal("0.01"), Decimal("-0.02"), Decimal("0.03"), Decimal("-0.01")]
    assert portfolio_volatility(rets) > 0
    assert portfolio_drawdown(rets) <= 0
    assert portfolio_volatility([]) == Decimal("0")


def test_portfolio_return_series_is_weighted():
    s = {
        "A": bars("A", ["100", "110"]),
        "B": bars("B", ["100", "100"]),
    }
    out = portfolio_return_series(s, {"A": Decimal("1"), "B": Decimal("0")})
    assert out == [Decimal("0.1")]


def test_annualised_return_compounds():
    assert annualised_return([Decimal("0.1"), Decimal("0.1")]) == pytest.approx(
        Decimal("21"), abs=Decimal("0.01")
    )


# ----------------------------------------------------------------------- snapshots


def test_snapshot_is_idempotent_per_day(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    day = date(2026, 9, 1)
    snap = Snapshot(
        day=day,
        total_usd=Decimal("1000"),
        positions_usd=Decimal("900"),
        cash_usd=Decimal("100"),
        cash_pct=Decimal("10"),
        weights={"NVDA": Decimal("90")},
        sector_weights={},
        country_weights={},
        currency_weights={"USD": Decimal("90")},
        hhi=Decimal("0.81"),
    )
    store.append(snap)
    store.append(snap)
    loaded = store.load()
    assert len(loaded) == 1, "same-day re-run must overwrite, not duplicate"
    assert loaded[0].total_usd == Decimal("1000")


def test_snapshot_roundtrip_preserves_weights(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    snap = Snapshot(
        day=date(2026, 9, 1),
        total_usd=Decimal("1000"),
        positions_usd=Decimal("900"),
        cash_usd=Decimal("100"),
        cash_pct=Decimal("10"),
        weights={"NVDA": Decimal("42.5")},
        sector_weights={"Info Tech": Decimal("42.5")},
        country_weights={"US": Decimal("42.5")},
        currency_weights={"USD": Decimal("42.5")},
        hhi=Decimal("0.18"),
        volatility_pct=Decimal("21.5"),
    )
    store.append(snap)
    back = store.latest()
    assert back is not None
    assert back.weights["NVDA"] == Decimal("42.5")
    assert back.currency_weights["USD"] == Decimal("42.5")
    assert back.volatility_pct == Decimal("21.5")


def test_series_extracts_history_for_charting(tmp_path: Path):
    store = SnapshotStore(tmp_path)
    for i in range(3):
        day = date(2026, 9, 1) + timedelta(days=i)
        store.append(
            Snapshot(
                day=day,
                total_usd=Decimal("1000"),
                positions_usd=Decimal("900"),
                cash_usd=Decimal("100"),
                cash_pct=Decimal("10"),
                weights={"NVDA": Decimal(str(40 + i))},
                sector_weights={},
                country_weights={},
                currency_weights={},
                hhi=Decimal("0.2"),
            )
        )
    pts = series(store.load(), "weight:NVDA")
    assert [p[1] for p in pts] == [Decimal("40"), Decimal("41"), Decimal("42")]
    assert len(series(store.load(), "hhi")) == 3


# --------------------------------------------------------------------------- HTML


def test_html_is_self_contained_and_offline():
    """Exit criterion: static HTML renders offline with no server."""
    html = render_html(exposure=compute(multi_market_view()), title="t")
    assert html.startswith("<!doctype html>")
    assert "<svg" in html
    assert "Not investment advice" in html
    # no external fetches of any kind
    assert not re.search(r'<(script|link)[^>]*\b(src|href)="https?://', html)
    assert "http://www.w3.org/2000/svg" in html  # namespace only, not a fetch


def test_html_includes_all_three_exposure_charts():
    html = render_html(exposure=compute(multi_market_view()))
    assert "By currency" in html and "By sector" in html and "By country" in html
    assert "FX summary" in html


def test_html_escapes_user_controlled_text():
    html = render_html(exposure=None, markdown_sections=[("Note", "<script>alert(1)</script>")])
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_line_chart_handles_single_point_and_empty():
    one = line_chart([ChartSeries("A", [("2026-01-01", Decimal("5"))])])
    assert "circle" in one
    assert "No history" in line_chart([ChartSeries("A", [])])
    assert "<em>" in line_chart([])


def test_bar_chart_scales_to_max():
    svg = bar_chart([("A", Decimal("50")), ("B", Decimal("100"))])
    assert "A" in svg and "B" in svg
    assert svg.count("<rect") == 2


def test_html_history_needs_two_snapshots(tmp_path: Path):
    snaps = [
        Snapshot(
            day=date(2026, 9, 1),
            total_usd=Decimal("1"),
            positions_usd=Decimal("1"),
            cash_usd=Decimal("0"),
            cash_pct=Decimal("0"),
            weights={},
            sector_weights={},
            country_weights={},
            currency_weights={},
            hhi=Decimal("0.5"),
        )
    ]
    html = render_html(snapshots=snaps)
    assert "Only one snapshot" in html


# ---------------------------------------------------------------------- CLI surface


def _seed_multi_market(root: Path) -> None:
    (root / "portfolio" / "ledger.md").write_text(
        "| date | ticker | side | quantity | price | currency | fees | reason | "
        "thesis_slug | fx_rate_usd_at_trade |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 2026-08-01 | NVDA | buy | 10 | 100 | USD | 0 | capex | - | - |\n"
        "| 2026-08-01 | 0700.HK | buy | 100 | 300 | HKD | 0 | tencent | - | - |\n"
        "| 2026-08-01 | VOLV-B.ST | buy | 50 | 200 | SEK | 0 | trucks | - | - |\n",
        encoding="utf-8",
    )
    (root / "portfolio" / "cash.md").write_text(
        "| currency | amount |\n| --- | --- |\n| USD | 1000 |\n| SEK | 1000 |\n",
        encoding="utf-8",
    )
    (root / "config" / "config.yaml").write_text(
        "base_currency: USD\n"
        "hkd_peg: '7.8'\n"
        "fx:\n  SEK: '0.095'\n"
        "models: {}\n"
        "sectors:\n  NVDA: Information Technology\n  0700.HK: Communication Services\n"
        "risk_rules:\n  - id: c\n    kind: max_position_weight\n    limit: '15'\n",
        encoding="utf-8",
    )


def test_cli_exposure_renders_all_dimensions(tmp_path: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    _seed_multi_market(root)
    cwd = Path.cwd()
    os.chdir(root)
    try:
        runner = CliRunner()
        runner.invoke(
            main,
            ["ingest", "NVDA", "0700.HK", "VOLV-B.ST", "--driver", "mock"],
            catch_exceptions=False,
        )
        out = runner.invoke(main, ["exposure"], catch_exceptions=False)
        assert out.exit_code == 0, out.output
        assert "Currency exposure" in out.output
        assert "SEK" in out.output and "HKD" in out.output
        assert "Sector exposure" in out.output
        assert "Country exposure" in out.output
    finally:
        os.chdir(cwd)


def test_cli_snapshot_then_report(tmp_path: Path):
    from click.testing import CliRunner

    from finlink.cli import main

    root = tmp_path / "ws"
    CliRunner().invoke(main, ["init", str(root)])
    _seed_multi_market(root)
    cwd = Path.cwd()
    os.chdir(root)
    try:
        runner = CliRunner()
        runner.invoke(
            main,
            ["ingest", "NVDA", "0700.HK", "VOLV-B.ST", "--driver", "mock"],
            catch_exceptions=False,
        )
        snap = runner.invoke(main, ["snapshot", "--no-commit"], catch_exceptions=False)
        assert snap.exit_code == 0, snap.output
        assert "snapshot" in snap.output.lower()

        rep = runner.invoke(main, ["report", "--with-correlation"], catch_exceptions=False)
        assert rep.exit_code == 0, rep.output
        assert (root / "reports" / "portfolio.html").exists()
        html = (root / "reports" / "portfolio.html").read_text(encoding="utf-8")
        assert "<svg" in html and "Correlation" in html
    finally:
        os.chdir(cwd)


def test_html_report_written_to_disk_is_valid(tmp_path: Path):
    from finlink.report.html import write as write_html

    target = write_html(
        tmp_path / "reports" / "p.html", render_html(exposure=compute(multi_market_view()))
    )
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_exposure_handles_unsuffixed_hk_codes():
    """Regression: classify_all keys by normalised ticker, so 00700 raised KeyError."""
    pos = Position(ticker="00700", currency="HKD")
    pos.add_lot(Lot(quantity=Decimal("700"), unit_cost=Decimal("300"), currency="HKD"))
    view = value_positions({"00700": pos}, {"00700": Decimal("400")}, {})
    report = compute(view)
    assert [b.name for b in report.currencies] == ["HKD"]
    assert [b.name for b in report.countries] == ["China"]
    assert report.unclassified_count == 1  # no sector mapping supplied


def test_exposure_counts_unclassified_for_normalised_tickers():
    """_sector_of must match 00700 against the 0700.HK key."""
    view = multi_market_view()
    report = compute(view, sectors={"0700.HK": "Communication Services"})
    # NVDA and VOLV-B.ST remain unmapped
    assert report.unclassified_count == 2


# ------------------------------------------- config mapping robustness (Phase 5+)


def test_config_key_matches_across_leading_zeros_and_suffix():
    """The user writes '00700'; classification works with '0700.HK'. Both must hit."""
    from finlink.domain.classify import classify

    for key in ("00700", "0700", "0700.HK", "700"):
        cls = classify(
            "00700.HK", "HKD", sectors={key: "Communication Services"}, countries={key: "China"}
        )
        assert cls.sector == "Communication Services", key
        assert cls.country == "China", key


def test_suffixed_config_key_does_not_match_a_different_exchange():
    """A Hong Kong mapping must not be inherited by a same-root US listing."""
    from finlink.domain.classify import classify

    cls = classify("BABA", "USD", countries={"0700.HK": "China"})
    assert cls.country == "US"


def test_broker_style_names_are_supported_by_suffixed_keys():
    """'INVESTOR B' never matched INVE-B.ST — the config now uses the symbol."""
    from finlink.domain.classify import classify

    cls = classify("INVE-B.ST", "SEK", sectors={"INVE-B.ST": "Financials"})
    assert cls.sector == "Financials"
    assert cls.country == "EU"


def test_every_configured_country_stays_in_the_three_buckets():
    """Regression: an unmapped ticker silently opened a fourth region."""
    import yaml

    from finlink.domain.classify import classify

    cfg_path = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    countries = {str(k): str(v) for k, v in (cfg.get("countries") or {}).items()}
    assert countries, "config has no countries mapping"
    for ticker in countries:
        cls = classify(ticker, "USD", countries=countries)
        assert cls.country in ("US", "China", "EU"), f"{ticker} -> {cls.country}"
