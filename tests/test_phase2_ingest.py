"""Phase 2: ingestion cache, quant maths, provenance, idempotency."""

from __future__ import annotations

import json
import os
import urllib.error
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
    return PriceBar(
        ticker=ticker, day=d, open=c, high=c, low=c, close=c, adj_close=c, volume=1000, currency=ccy
    )


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
        return NewsItem(
            ticker="X",
            title=f"t{n}",
            url=f"https://e.com/{n}",
            published_at="2026-01-01",
            source="s",
        )

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
        cwd=root,
        check=True,
        capture_output=True,
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


# ---------------- RSS news driver ----------------

def _fake_entry(link: str, title: str, summary: str = "", published: str = "") -> object:
    return type(
        "Entry",
        (),
        {
            "link": link,
            "title": title,
            "summary": summary,
            "published": published,
            "published_parsed": (2026, 9, 1, 12, 0, 0, 0, 1, -1),
            "updated_parsed": None,
        },
    )()


def test_rss_news_driver_parses_entries(monkeypatch):
    import finlink.ingest.rss_news_driver as rss_mod

    monkeypatch.setattr(
        rss_mod.feedparser,
        "parse",
        lambda url: type(
            "Feed",
            (),
            {
                "entries": [
                    _fake_entry("https://e.co/1", "Alpha news one"),
                    _fake_entry("", "no link — discarded"),
                    _fake_entry("https://e.co/2", "Alpha news two"),
                ],
                "feed": {"title": "Test Feed"},
            },
        )(),
    )
    driver = rss_mod.RSSNewsDriver(feeds=["https://example.com/feed?s={query}"])
    items = driver.fetch_news("AAPL")
    assert len(items) == 2
    assert items[0].ticker == "AAPL"
    assert items[0].published_at  # non-empty ISO ts from published_parsed


def test_rss_news_driver_hk_symbol_norm(monkeypatch):
    import finlink.ingest.rss_news_driver as rss_mod

    seen = {}

    def fake_parse(url):
        seen["url"] = url
        return type("Feed", (), {"entries": [], "feed": {"title": ""}})()

    monkeypatch.setattr(rss_mod.feedparser, "parse", fake_parse)
    driver = rss_mod.RSSNewsDriver(feeds=["https://feed/{query}"])
    driver.fetch_news("00700.HK")
    # Ticker has a company query override; the URL must carry the encoded name.
    assert "Tencent" in seen["url"] and "00700" in seen["url"]


# ---------------- Alpha Vantage multi-key ------------------

def test_alphavantage_requires_key():
    from finlink.ingest.alphavantage_driver import AlphaVantageDriver
    with pytest.raises(IngestError, match="needs an API key"):
        AlphaVantageDriver("")  # type: ignore[arg-type]


def test_alphavantage_rotate_keys_on_429(monkeypatch):
    """When the first key 429s, the driver falls over to the second key."""
    import finlink.ingest.alphavantage_driver as av

    calls: list[str] = []
    resp = {
        "Time Series (Daily)": {
            "2026-09-01": {
                "1. open": "100", "2. high": "101", "3. low": "99",
                "4. close": "100.5", "5. volume": "1000",
            }
        }
    }

    def fake_urlopen(req, timeout):
        key = req.full_url.split("apikey=")[1].split("&")[0]
        calls.append(key)
        if key == "KEY1":
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, None)
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(resp).encode()

        return Resp()

    monkeypatch.setattr(av.urllib.request, "urlopen", fake_urlopen)
    # bypass throttle sleeps
    d = av.AlphaVantageDriver(["KEY1", "KEY2"])
    d.MIN_INTERVAL_S = 0.0
    bars = d.fetch_prices("AAPL", days=90)
    assert len(bars) == 1
    assert bars[0].close == Decimal("100.5")
    assert calls == ["KEY1", "KEY2"]  # fell over exactly once


def test_alphavantage_hk_symbol_keeps_hk_suffix():
    """Alpha Vantage accepts 4-digit .HK symbols directly (no .HKG rewrite)."""
    from finlink.ingest.alphavantage_driver import AlphaVantageDriver

    d = AlphaVantageDriver("KEY")
    assert d._symbol("0700.HK") == "0700.HK"
    assert d._symbol("00981.HK") == "0981.HK"  # left-pad, no leading zero
    assert d._symbol("01810.HK") == "1810.HK"
    assert d._symbol("INVE-B.ST") == "INVE-B.ST"  # Stockholm untouched


def test_alphavantage_rotate_keys_on_daily_cap_json(monkeypatch):
    """AV answers a spent key with HTTP 200 + daily-cap JSON; fall to next key."""
    import finlink.ingest.alphavantage_driver as av

    calls: list[str] = []
    resp = {
        "Time Series (Daily)": {
            "2026-09-01": {
                "1. open": "100", "2. high": "101", "3. low": "99",
                "4. close": "100.5", "5. volume": "1000",
            }
        }
    }
    cap_json = {
        "Information": "We have detected your API key and our standard API rate "
        "limit is 25 requests per day."
    }

    def fake_urlopen(req, timeout):
        key = req.full_url.split("apikey=")[1].split("&")[0]
        calls.append(key)
        payload = cap_json if key == "KEY1" else resp

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode()

        return Resp()

    monkeypatch.setattr(av.urllib.request, "urlopen", fake_urlopen)
    d = av.AlphaVantageDriver(["KEY1", "KEY2"])
    d.MIN_INTERVAL_S = 0.0
    bars = d.fetch_prices("AAPL", days=90)
    assert len(bars) == 1
    assert calls == ["KEY1", "KEY2"]


# ---------------- Tencent driver (free, keyless HK + US) -------------

def test_tencent_symbol_mapping():
    from finlink.ingest.tencent_driver import TencentDriver

    d = TencentDriver()
    assert d._symbol("00700.HK") == "hk00700"
    assert d._symbol("00981.HK") == "hk00981"
    assert d._symbol("01810.HK") == "hk01810"
    assert d._symbol("02359.HK") == "hk02359"
    assert d._symbol("0700.HK") == "hk00700"
    assert d._symbol("BABA") == "usBABA.N"
    assert d._symbol("GLD") == "usGLD.AM"
    assert d._symbol("SGOV") == "usSGOV.N"
    assert d._currency("00700.HK") == "HKD"
    assert d._currency("BABA") == "USD"


def test_tencent_rejects_unsupported():
    from finlink.ingest.tencent_driver import IngestError as E
    from finlink.ingest.tencent_driver import TencentDriver

    d = TencentDriver()
    for bad in ["INVE-B.ST", "LUG.ST", "XYZ.ST", ""]:
        with pytest.raises(E):
            d._symbol(bad)


def test_tencent_parses_kline_rows(monkeypatch):
    import finlink.ingest.tencent_driver as tc

    rows = [
        ["2026-09-01", "100.0", "101.5", "102.0", "99.5", "1000"],
        ["2026-09-02", "101.0", "99.0", "102.2", "98.0", "2000"],
    ]

    def fake_request_json(self, url):
        return {"data": {"hk00981": {"day": rows}}}

    monkeypatch.setattr(tc.TencentDriver, "_request_json", fake_request_json)
    d = tc.TencentDriver(delay=0)
    bars = d.fetch_prices("00981.HK", days=10)
    assert len(bars) == 2
    assert bars[0].day.isoformat() == "2026-09-01"
    assert bars[0].open == Decimal("100.0")
    assert bars[0].high == Decimal("102.0")
    assert bars[0].low == Decimal("99.5")
    assert bars[0].close == Decimal("101.5")
    assert bars[0].adj_close == Decimal("101.5")
    assert bars[0].volume == 1000
    assert bars[0].currency == "HKD"
    assert bars[-1].close == Decimal("99.0")


def test_tencent_empty_series_raises(monkeypatch):
    import finlink.ingest.tencent_driver as tc

    monkeypatch.setattr(
        tc.TencentDriver,
        "_request_json",
        lambda self, url: {"data": {"usAAPL.N": {}}},
    )
    d = tc.TencentDriver(delay=0)
    with pytest.raises(tc.IngestError, match="no day series"):
        d.fetch_prices("AAPL", days=10)


def test_tencent_bare_us_uses_mic_default(monkeypatch):
    """A bare US ticker without a mapping must still get the .N MIC suffix."""
    from finlink.ingest.tencent_driver import TencentDriver

    d = TencentDriver()
    assert d._symbol("AAPL") == "usAAPL.N"
    assert d._symbol("NVDA") == "usNVDA.N"
