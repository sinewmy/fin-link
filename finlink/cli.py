"""finlink CLI — the only component permitted to write to the workspace."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import click

from finlink import doctor as doctor_mod
from finlink.config import RuntimeConfig
from finlink.llm.client import LLMClient
from finlink.workspace import find_root, load_workspace

CONTEXT = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT)
@click.version_option(package_name="finlink")
def main() -> None:
    """fin-link — markdown-first investment decision journal."""


@main.command()
@click.argument("directory", required=False, type=click.Path(path_type=Path))
def init(directory: Path | None) -> None:
    """Create a new fin-link workspace."""
    target = (directory or Path.cwd()).resolve()
    target.mkdir(parents=True, exist_ok=True)
    for sub in [
        "portfolio", "theses", "reviews", "config",
        "data/prices", "data/metrics", "data/news", "logs",
    ]:
        (target / sub).mkdir(parents=True, exist_ok=True)
    (target / ".finlink").write_text("fin-link workspace\n", encoding="utf-8")
    # Append, never overwrite: a project may already have a .gitignore (this repo does),
    # and replacing it would silently commit .venv/ and friends.
    gitignore = target / ".gitignore"
    needed = ["data/", "logs/"]
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    missing = [n for n in needed if n not in existing.splitlines()]
    if missing:
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        gitignore.write_text(existing + prefix + "\n".join(missing) + "\n", encoding="utf-8")
        click.echo(f"gitignore: added {', '.join(missing)}")

    if not (target / "config" / "config.yaml").exists():
        (target / "config" / "config.yaml").write_text(
            "# fin-link configuration\n"
            "base_currency: USD\n"
            "hkd_peg: '7.8'          # HKD is pegged; do not float it\n"
            "fx:\n"
            "  SEK: '0.095'          # 1 SEK = 0.095 USD — refresh with `finlink fx-set`\n"
            "models: {}              # fill per pipeline, e.g. decompose: openai/gpt-4o-mini\n"
            "risk_rules: []\n",
            encoding="utf-8",
        )
    for name, header in [
        (
            "positions.md",
            "| ticker | quantity | avg_cost | currency | opened_at | thesis_slug | notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n",
        ),
        (
            "ledger.md",
            "| date | ticker | side | quantity | price | currency | fees | reason | "
            "thesis_slug | fx_rate_usd_at_trade |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n",
        ),
        ("cash.md", "| currency | amount |\n| --- | --- |\n| USD | 0 |\n"),
    ]:
        f = target / "portfolio" / name
        if not f.exists():
            f.write_text(header, encoding="utf-8")
    click.echo(f"initialised fin-link workspace at {target}")


@main.command()
def doctor() -> None:
    """Validate every tracked file against the schema. Fails loud; never auto-repairs."""
    root = find_root()
    ok = doctor_mod.run(root)
    raise SystemExit(0 if ok else 1)


@main.command()
def show() -> None:
    """Render the portfolio: weights, cost basis, unrealised PnL (USD-normalised)."""
    from finlink.ingest.pipeline import latest_prices
    from finlink.ingest.store import Store
    from finlink.render import render_portfolio

    root = find_root()
    ws = load_workspace(root)
    held = sorted({p.ticker for p in ws.positions} | {t.ticker for t in ws.ledger})
    prices = latest_prices(Store(root), held)
    click.echo(render_portfolio(ws, prices))


def _get_driver(root: Path, driver_override: str | None = None) -> tuple[LLMClient, RuntimeConfig]:
    """Resolve an LLM driver. `echo` runs offline at zero cost."""
    from finlink.config import RuntimeConfig
    from finlink.llm.base import LLMRunLog
    from finlink.llm.client import LLMClient
    from finlink.llm.drivers.echo import EchoDriver
    from finlink.llm.drivers.openrouter import OpenRouterDriver

    cfg = RuntimeConfig.load(root)
    name = (driver_override or cfg.driver or "openrouter").lower()
    if name == "echo":
        return LLMClient(EchoDriver(), LLMRunLog(root / "logs" / "llm_runs.jsonl"), "echo"), cfg
    if name == "openrouter":
        return (
            LLMClient(
                OpenRouterDriver(cfg.openrouter_api_key),
                LLMRunLog(root / "logs" / "llm_runs.jsonl"),
            ),
            cfg,
        )
    raise click.ClickException(f"unknown driver {name!r} (expected openrouter or echo)")


@main.command()
@click.option("--ticker", required=True)
@click.option("--side", required=True, type=click.Choice(["buy", "sell"]))
@click.option("--quantity", required=True)
@click.option("--price", required=True)
@click.option("--currency", default="USD", show_default=True)
@click.option("--date", "traded_at", default=None, help="ISO date; defaults to today")
@click.option("--reason", required=True, help="Why — in your own words")
@click.option("--horizon", default="", help="e.g. 2y, 6m")
@click.option("--invalid-if", "invalidation", default="", help="What would prove you wrong")
@click.option("--fees", default="0")
@click.option("--driver", default=None, help="openrouter (default) or echo (offline)")
@click.option("--slug", default="")
@click.option("--no-commit", is_flag=True)
def record_trade(
    ticker: str, side: str, quantity: str, price: str, currency: str, traded_at: str | None,
    reason: str, horizon: str, invalidation: str, fees: str, driver: str | None,
    slug: str, no_commit: bool,
) -> None:
    """Append a trade to the ledger and draft a thesis (status: draft)."""
    from datetime import date

    from finlink.gitutil import commit
    from finlink.io.markdown import read_document
    from finlink.llm.pipelines.decompose import DecomposeInput
    from finlink.llm.pipelines.decompose import run as decompose_run

    root = find_root()
    day = date.fromisoformat(traded_at) if traded_at else date.today()

    row = (
        f"| {day.isoformat()} | {ticker} | {side} | {quantity} | {price} | {currency.upper()} "
        f"| {fees} | {reason} | - | - |"
    )
    ledger = root / "portfolio" / "ledger.md"
    text = ledger.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    ledger.write_text(text + row + "\n", encoding="utf-8")
    click.echo(f"ledger: appended {side} {quantity} {ticker}")

    try:
        client, cfg = _get_driver(root, driver)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    if side == "sell":
        click.echo("sell recorded (no new thesis for sells)")
    else:
        try:
            # Fail with an actionable message before spending an API call.
            model = cfg.model_for("P1_decompose") if driver != "echo" else "echo"
            result = decompose_run(
                DecomposeInput(ticker=ticker, reason=reason, horizon=horizon, slug=slug),
                client=client,
                theses_dir=root / "theses",
                model=model,
            )
        except (FileExistsError, RuntimeError) as e:
            raise click.ClickException(str(e)) from e
        click.echo(f"\nthesis draft: {result.path}")
        doc = read_document(result.path)
        click.echo(doc.body)
        click.echo(f"\nReview it, then: finlink confirm {result.path}")

    if not no_commit:
        sha = commit(root, f"finlink record-trade {ticker} {side} {quantity}")
        if sha:
            click.echo(f"committed {sha}")


@main.command()
@click.argument("thesis_path", type=click.Path(path_type=Path))
@click.option("--no-commit", is_flag=True)
def confirm(thesis_path: Path, no_commit: bool) -> None:
    """Approve a drafted thesis: status draft -> active."""
    from finlink.gitutil import commit
    from finlink.io.markdown import read_document, set_frontmatter_key

    root = find_root()
    target = thesis_path if thesis_path.is_absolute() else (root / thesis_path)
    if not target.exists():
        raise click.ClickException(f"no such thesis: {target}")
    current = read_document(target).frontmatter.get("status")
    if current != "draft":
        raise click.ClickException(f"thesis status is {current!r}, expected 'draft'")
    set_frontmatter_key(target, "status", "active")
    click.echo(f"confirmed: {target.name} is now active")
    if not no_commit:
        sha = commit(root, f"finlink confirm {target.name}")
        if sha:
            click.echo(f"committed {sha}")


@main.command("set-frontmatter")
@click.argument("thesis_path", type=click.Path(path_type=Path))
@click.option("--key", required=True)
@click.option("--value", required=True)
@click.option("--no-commit", is_flag=True)
def set_frontmatter(thesis_path: Path, key: str, value: str, no_commit: bool) -> None:
    """Mutate ONE named frontmatter key. Never rewrites the body."""
    import json

    from finlink.gitutil import commit
    from finlink.io.markdown import set_frontmatter_key

    root = find_root()
    target = thesis_path if thesis_path.is_absolute() else (root / thesis_path)
    if not target.exists():
        raise click.ClickException(f"no such thesis: {target}")
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    set_frontmatter_key(target, key, parsed)
    click.echo(f"set {key} = {parsed!r} on {target.name}")
    if not no_commit:
        sha = commit(root, f"finlink set-frontmatter {target.name} {key}")
        if sha:
            click.echo(f"committed {sha}")



def _drivers(root: Path, driver_override: str | None = None) -> tuple[object, object | None]:
    """Resolve market-data + news drivers. `mock` runs fully offline."""
    from finlink.config import RuntimeConfig
    from finlink.ingest.mock import MockMarketDriver, MockNewsDriver
    from finlink.ingest.yfinance_driver import YFinanceDriver

    cfg = RuntimeConfig.load(root)
    name = (driver_override or cfg.driver or "yfinance").lower()
    if name == "mock":
        # strict: unknown tickers fail instead of silently inventing prices
        return MockMarketDriver(strict=True), MockNewsDriver()
    if name in ("yfinance", "openrouter"):
        return YFinanceDriver(), None
    if name == "echo":
        return MockMarketDriver(), MockNewsDriver()
    raise click.ClickException(f"unknown ingest driver {name!r} (expected yfinance or mock)")


@main.command()
@click.argument("tickers", nargs=-1)
@click.option("--driver", default=None, help="yfinance (default) or mock (offline)")
@click.option("--days", default=400, show_default=True)
def ingest(tickers: tuple[str, ...], driver: str | None, days: int) -> None:
    """Fetch prices, fundamentals and news into the data/ cache (idempotent)."""
    from finlink.ingest.pipeline import ingest_ticker, log_run
    from finlink.ingest.store import Store
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    held = {p.ticker for p in ws.positions} | {t.ticker for t in ws.ledger}
    wanted = list(tickers) or sorted(held)
    if not wanted:
        raise click.ClickException("no tickers given and none found in portfolio")

    store = Store(root)
    store.ensure()
    market, news = _drivers(root, driver)
    results = [
        ingest_ticker(t, store, market, news, days=days)  # type: ignore[arg-type]
        for t in wanted
    ]
    log_run(root, results)

    failed = False
    click.echo(f"{'ticker':<14}{'added':>7}{'total':>7}{'news':>7}  {'ccy':<5}status")
    for r in results:
        status = "ok" if not r.error else f"FAILED: {r.error[:60]}"
        failed = failed or bool(r.error)
        click.echo(
            f"{r.ticker:<14}{r.prices_added:>7}{r.prices_total:>7}{r.news_added:>7}  "
            f"{r.currency:<5}{status}"
        )
    if failed:
        raise SystemExit(1)


@main.command()
@click.argument("tickers", nargs=-1, required=True)
@click.option("--driver", default=None)
def onboard(tickers: tuple[str, ...], driver: str | None) -> None:
    """Verify a ticker resolves before trusting it.

    Fails loud on coverage gaps (HK and some Swedish names have Yahoo gaps) rather
    than storing nulls.
    """
    from finlink.ingest.store import Store

    root = find_root()
    store = Store(root)
    store.ensure()
    market, _ = _drivers(root, driver)
    bad = 0
    for t in tickers:
        try:
            bars = market.fetch_prices(t, days=30)  # type: ignore[attr-defined]
            fund = market.fetch_fundamentals(t)  # type: ignore[attr-defined]
            if not bars:
                raise RuntimeError("no price data returned")
            click.echo(
                f"OK   {t:<12} ccy={fund.currency:<4} last={bars[-1].close} "
                f"as_of={bars[-1].day.isoformat()}"
            )
        except Exception as e:  # noqa: BLE001
            bad += 1
            click.echo(f"FAIL {t:<12} {e}", err=True)
    if bad:
        raise SystemExit(1)


@main.command()
@click.argument("ticker")
def quote(ticker: str) -> None:
    """Show cached price and computed metrics for a ticker."""
    from finlink.domain.quant import summarise
    from finlink.ingest.store import Store

    root = find_root()
    store = Store(root)
    bars = store.load_prices(ticker)
    if not bars:
        raise click.ClickException(f"no cached prices for {ticker}; run `finlink ingest {ticker}`")
    s = summarise(bars)
    assert s is not None
    fund = store.load_fundamentals(ticker)
    click.echo(f"{ticker}  last={s.last_price} {s.currency}  as_of={s.as_of}")
    click.echo(f"  return(period) {s.period_return_pct:.2f}%")
    click.echo(f"  volatility(ann) {s.annualised_vol_pct:.2f}%")
    click.echo(f"  max drawdown    {s.max_drawdown_pct:.2f}%")
    if s.sma_50:
        click.echo(f"  sma50           {s.sma_50:.4f}")
    if s.sma_200:
        click.echo(f"  sma200          {s.sma_200:.4f}")
    if fund and fund.metrics:
        click.echo("  fundamentals:")
        for k, v in sorted(fund.metrics.items()):
            click.echo(f"    {k:<22}{v}")


@main.command()
@click.argument("ticker")
@click.option("--limit", default=10, show_default=True)
def news(ticker: str, limit: int) -> None:
    """List cached news for a ticker (every item has a source URL)."""
    from finlink.ingest.store import Store

    root = find_root()
    items = Store(root).load_news(ticker)
    if not items:
        raise click.ClickException(f"no cached news for {ticker}; run `finlink ingest {ticker}`")
    for it in list(reversed(items))[:limit]:
        click.echo(f"{it.published_at}  {it.title}\n    {it.url}")


@main.command("fx-update")
def fx_update() -> None:
    """Refresh FX rates from Frankfurter (SEK; HKD stays on its configured peg)."""
    import yaml

    from finlink.ingest.fx import fetch_rate_to_usd

    root = find_root()
    cfg_path = root / "config" / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    fx = raw.get("fx") or {}
    # HKD is pegged by design; a live rate would silently override the peg in
    # build_rates() and contradict the "do not float it" rule.
    for ccy in ("SEK", "HKD"):
        if ccy == "HKD" and raw.get("hkd_peg"):
            fx.pop("HKD", None)
            click.echo(f"HKD: left on the {raw['hkd_peg']} peg (config hkd_peg)")
            continue
        try:
            rate, as_of = fetch_rate_to_usd(ccy)
            fx[ccy] = str(rate)
            click.echo(f"{ccy}: 1 {ccy} = {rate} USD (as of {as_of or 'unknown'})")
        except Exception as e:  # noqa: BLE001
            click.echo(f"{ccy}: FAILED — {e}", err=True)
    raw["fx"] = fx
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


@main.command("cost-report")
def cost_report() -> None:
    """Summarise LLM spend from logs/llm_runs.jsonl."""
    import json
    from collections import defaultdict

    root = find_root()
    path = root / "logs" / "llm_runs.jsonl"
    if not path.exists():
        click.echo("no LLM runs logged yet")
        return
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"runs": 0, "ok": 0, "err": 0, "in": 0, "out": 0}
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        m = by_model[r.get("model") or "?"]
        m["runs"] += 1
        m["ok"] += r.get("status") == "ok"
        m["err"] += r.get("status") == "error"
        m["in"] += r.get("tokens_in") or 0
        m["out"] += r.get("tokens_out") or 0
    click.echo(f"{'model':<32}{'runs':>6}{'ok':>5}{'err':>5}{'tok_in':>9}{'tok_out':>9}")
    for model, v in sorted(by_model.items()):
        click.echo(
            f"{model:<32}{int(v['runs']):>6}{int(v['ok']):>5}{int(v['err']):>5}"
            f"{int(v['in']):>9}{int(v['out']):>9}"
        )


@main.command("fx-set")
@click.argument("currency")
@click.argument("rate")
def fx_set(currency: str, rate: str) -> None:
    """Set an FX rate to USD, e.g. `finlink fx-set SEK 0.095`."""
    import yaml

    root = find_root()
    cfg_path = root / "config" / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    fx = raw.get("fx") or {}
    fx[currency.upper()] = str(Decimal(rate))
    raw["fx"] = fx
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    click.echo(f"set {currency.upper()} = {rate} USD")


if __name__ == "__main__":
    main()
