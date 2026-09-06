"""Phase 2: ingestion cache, quant maths, provenance, idempotency."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from finlink.domain.quant import (
    annualised_volatility,
    max_drawdown,
    simple_return,
    sma,
    summarise,
)
from finlink.ingest.base import IngestError, NewsItem, PriceBar
from finlink.ingest.mock import MockMarketDriver, MockNewsDriver
from finlink.ingest.pipeline import ingest_ticker, latest_prices
from finlink.ingest.store import Store


def bar(day_offset: int, close: str, ticker: str = "X", ccy: str = "USD") -> PriceBar:
    d = date(2026, 1, 1) + timedelta(days=day_offset)
    c = Decimal(close)
    return PriceBar(ticker=ticker, day=d, open=c, high=c, low=c, close=c,
                    adj_close=c, volume=1000, currency=ccy)


def test_simple_return():
    bars = [bar(0, "100"), bar(1, "110")]
    assert simple_return(bars) == Decimal("10")


def test_simple_return_single_bar_is_zero():
    assert simple_return([bar(0, "100")]) == Decimal("0")


def test_volatility_zero_for_flat_series():
    assert annualised_volatility([bar(i, "100") for i in range(5)]) == Decimal("0")


def test_volatility_positive_for_moving_series():
    bars = [bar(i, str(100 + (i % 2) * 10)) for i in range(20)]
    assert annualised_volatility(bars) > 0


def test_max_drawdown_is_negative():
    bars = [bar(0, "100"), bar(1, "120"), bar(2, "60"), bar(3, "90")]
    dd = max_drawdown(bars)
    assert dd < 0
    assert dd == Decimal("-50")  # 120 -> 60


def test_max_drawdown_zero_when_monotonic_up():
    assert max_drawdown([bar(i, str(100 + i)) for i in range(5)]) == Decimal("0")


def test_sma_needs_full_window():
    bars = [bar(i, str(100 + i)) for i in range(10)]
    assert sma(bars, 5) is not None
    assert sma(bars, 50) is None


def test_summarise_on_empty_is_none():
    assert summarise([]) is None


def test_store_prices_roundtrip(tmp_path: Path):
    store = Store(tmp_path)
    store.ensure()
    bars = [bar(0, "100"), bar(1, "101")]
    added, total = store.append_prices(bars)
    assert (added, total) == (2, 2)
    loaded = store.load_prices("X")
    assert len(loaded) == 2
    assert loaded[-1].close == Decimal("101")


def test_store_prices_idempotent(tmp_path: Path):
    """Re-ingesting must not duplicate — the cache is re-runnable."""
    store = Store(tmp_path)
    store.ensure()
    store.append_prices([bar(0, "100"), bar(1, "101")])
    added, total = store.append_prices([bar(0, "100"), bar(1, "101")])
    assert added == 0
    assert total == 2


def test_store_appends_new_days(tmp_path: Path):
    store = Store(tmp_path)
    store.ensure()
    store.append_prices([bar(0, "100")])
    added, total = store.append_prices([bar(0, "100"), bar(1, "102")])
    assert added == 1
    assert total == 2


def test_news_without_url_is_discarded(tmp_path: Path):
    """Provenance rule: no resolvable source URL -> not stored."""
    store = Store(tmp_path)
    store.ensure()
    items = [
        NewsItem(
            ticker="X",
            title="has url",
            url="https://e.com/a",
            published_at="2026-01-01",
            source="s",
        ),
        NewsItem(ticker="X", title="no url", url="", published_at="2026-01-02", source="s"),
        NewsItem(ticker="X", title="bad url", url="notaurl", published_at="2026-01-03", source="s"),
    ]
    assert store.append_news(items) == 1
    assert len(store.load_news("X")) == 1


def test_news_dedupes_by_url(tmp_path: Path):
    store = Store(tmp_path)
    store.ensure()
    def it(n):
        return NewsItem(ticker="X", title=f"t{n}", url=f"https://e.com/{n}",
                                published_at="2026-01-01", source="s")
    store.append_news([it("a"), it("b")])
    assert store.append_news([it("a"), it("c")]) == 1
    assert len(store.load_news("X")) == 3


def test_mock_driver_unknown_ticker_in_strict_mode():
    drv = MockMarketDriver(strict=True)
    with pytest.raises(IngestError, match="not in mock dataset"):
        drv.fetch_prices("NOPE")


def test_mock_driver_currency_by_market():
    assert MockMarketDriver().fetch_prices("VOLV-B.ST")[0].currency == "SEK"
    assert MockMarketDriver().fetch_prices("0700.HK")[0].currency == "HKD"
    assert MockMarketDriver().fetch_prices("AAPL")[0].currency == "USD"


def test_ingest_ticker_records_error_not_crash(tmp_path: Path):
    class BrokenDriver:
        name = "broken"

        def fetch_prices(self, ticker, days=400):
            raise RuntimeError("provider down")

        def fetch_fundamentals(self, ticker):
            raise RuntimeError("provider down")

    store = Store(tmp_path)
    store.ensure()
    res = ingest_ticker("X", store, BrokenDriver())  # type: ignore[arg-type]
    assert res.error is not None
    assert "provider down" in res.error
    assert res.prices_total == 0


def test_ingest_end_to_end_with_mock(tmp_path: Path):
    store = Store(tmp_path)
    store.ensure()
    res = ingest_ticker("AAPL", store, MockMarketDriver(strict=True), MockNewsDriver())
    assert res.error is None
    assert res.prices_total == 400
    assert res.news_added == 5
    assert res.currency == "USD"
    assert store.latest_price("AAPL") is not None


def test_latest_prices_skips_missing(tmp_path: Path):
    store = Store(tmp_path)
    store.ensure()
    store.append_prices([bar(0, "100", ticker="A")])
    prices = latest_prices(store, ["A", "MISSING"])
    assert set(prices) == {"A"}
    assert prices["A"] == Decimal("100")


def test_cache_is_disposable_and_restorable(tmp_path: Path):
    """Deleting data/ then re-ingesting must fully restore it."""
    store = Store(tmp_path)
    store.ensure()
    first = ingest_ticker("AAPL", store, MockMarketDriver(strict=True), MockNewsDriver())
    before = store.latest_price("AAPL")

    import shutil
    shutil.rmtree(tmp_path / "data")

    store2 = Store(tmp_path)
    store2.ensure()
    second = ingest_ticker("AAPL", store2, MockMarketDriver(strict=True), MockNewsDriver())
    assert second.error is None
    assert store2.latest_price("AAPL") == before
    assert second.prices_total == first.prices_total


def test_hkd_peg_is_not_overridden_by_config_fx(tmp_path: Path):
    """Regression: fx-update wrote a live HKD rate that silently beat the peg."""
    import subprocess
    import sys

    import yaml

    root = Path(__file__).resolve().parents[1]
    ws = tmp_path / "ws"
    subprocess.run(
        [sys.executable, "-m", "finlink.cli", "init", str(ws)],
        cwd=root, check=True, capture_output=True,
    )
    cfg_path = ws / "config" / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    raw["hkd_peg"] = "7.8"
    raw["fx"] = {"SEK": "0.095", "HKD": "0.13"}  # a live HKD rate
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    from click.testing import CliRunner

    from finlink.cli import main

    runner = CliRunner()
    cwd = Path.cwd()
    try:
        os.chdir(ws)  # CLI resolves the workspace from cwd
        # Network may be unavailable offline; what matters is the peg is preserved.
        runner.invoke(main, ["fx-update"], catch_exceptions=True)
    finally:
        os.chdir(cwd)

    final = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "HKD" not in (final.get("fx") or {}), (
        "HKD must stay on the configured peg, not a live rate"
    )
