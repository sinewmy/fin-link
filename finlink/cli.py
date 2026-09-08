"""finlink CLI — the only component permitted to write to the workspace."""

from __future__ import annotations

import re
from datetime import date as date_cls
from decimal import Decimal
from pathlib import Path

import click

from finlink import doctor as doctor_mod
from finlink.config import RuntimeConfig
from finlink.domain.alerts import AlertRecord
from finlink.domain.pnl import Lot, Position
from finlink.domain.portfolio import PortfolioView
from finlink.domain.risk import Alert
from finlink.ingest.store import Store
from finlink.io.markdown import MarkdownError
from finlink.llm.client import LLMClient
from finlink.llm.pipelines.review import ReviewResult
from finlink.models import CashEntry
from finlink.workspace import LoadedWorkspace, find_root, load_workspace

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
        "portfolio",
        "theses",
        "reviews",
        "config",
        "data/prices",
        "data/metrics",
        "data/news",
        "logs",
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
    ticker: str,
    side: str,
    quantity: str,
    price: str,
    currency: str,
    traded_at: str | None,
    reason: str,
    horizon: str,
    invalidation: str,
    fees: str,
    driver: str | None,
    slug: str,
    no_commit: bool,
) -> None:
    """Append a trade to the ledger and sync positions.md + cash.md.

    Positions and cash are derived FROM the ledger (source of truth), so every
    record-trade keeps the three registries consistent in one atomic commit.
    """
    from datetime import date

    from finlink.gitutil import commit
    from finlink.io.markdown import read_document
    from finlink.ledger_sync import (
        apply_cash_delta,
        cash_delta,
        positions_from_ledger,
        write_cash,
        write_positions,
    )
    from finlink.llm.pipelines.decompose import DecomposeInput
    from finlink.llm.pipelines.decompose import run as decompose_run
    from finlink.models import LedgerRow

    root = find_root()
    day = date.fromisoformat(traded_at) if traded_at else date.today()

    # Pre-flight BEFORE mutating anything: derive the slug so a collision fails
    # without leaving a half-written ledger row behind (no API call needed for sells).
    theses = load_workspace(root).theses
    proposed_slug = slug or _deterministic_slug(reason)
    if side == "buy":
        for _path, tf in theses:
            if tf.ticker.upper() == ticker.upper() and tf.slug == proposed_slug:
                raise click.ClickException(
                    f"thesis already exists for {ticker} slug {proposed_slug!r}: "
                    f"{_path.name}\n"
                    "If this is the SAME thesis, link the new buy to it (no new file).\n"
                    "If you changed your mind, pass --slug <different>."
                )

    # Build a LedgerRow from the trade inputs (same values the md row stores) so
    # we never parse the file back for arithmetic.
    trade = LedgerRow.model_validate(
        {
            "date": day,
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "currency": currency.upper(),
            "fees": fees or None,
            "reason": reason,
        }
    )

    # 1. Append the trade to the ledger.
    ledger = root / "portfolio" / "ledger.md"
    text = ledger.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    ledger.write_text(
        text
        + _ledger_row(day, ticker, side, quantity, price, currency, fees, reason)
        + "\n",
        encoding="utf-8",
    )
    click.echo(f"ledger: appended {side} {quantity} {ticker}")

    # 2. Re-derive positions from the (now updated) ledger; cash adjusts
    # incrementally (cash.md is the CURRENT balance, not replayable history).
    ws = load_workspace(root)
    pos_rows = positions_from_ledger(ws.ledger)
    cash_rows = apply_cash_delta(
        [_cash_row(c) for c in ws.cash], cash_delta(trade), trade.currency.upper()
    )
    write_positions(root, pos_rows)
    write_cash(root, cash_rows)
    click.echo("positions.md + cash.md synced from ledger")

    if side == "sell":
        click.echo("sell recorded (no new thesis for sells)")
    else:
        try:
            client, cfg = _get_driver(root, driver)
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


def _deterministic_slug(reason: str) -> str:
    from finlink.domain.slug import slugify

    return slugify(reason)


def _ledger_row(
    day: date_cls,
    ticker: str,
    side: str,
    quantity: str,
    price: str,
    currency: str,
    fees: str,
    reason: str,
) -> str:
    return (
        f"| {day.isoformat()} | {ticker} | {side} | {quantity} | {price} | {currency.upper()} "
        f"| {fees} | {reason} | - | - |"
    )


def _cash_row(entry: CashEntry) -> dict[str, str]:
    return {"currency": entry.currency, "amount": str(entry.amount)}


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


@main.command("seed-ledger")
@click.option("--no-commit", is_flag=True)
@click.option("--from-cash", is_flag=True,
              help="Also seed cash.md balances as initial ledger context")
def seed_ledger(no_commit: bool, from_cash: bool) -> None:
    """Turn hand-seeded positions.md + cash.md into opening ledger rows.

    Brings a pre-existing (hand-maintained) portfolio into ledger-as-truth so that
    `record-trade` can keep positions + cash in sync. Idempotent: refuses to run
    when ledger.md already has rows.

    Each position becomes a `buy` row on its `opened_at` (or --date), with the
    thesis_slug carried over. Cash balances are preserved as-is (they are not
    history; there is no prior ledger to derive them from), so cash.md stays
    authoritative — this seed only ADDS the holding history, it does not replay
    cash flows.
    """
    from finlink.gitutil import commit
    from finlink.io.markdown import parse_table
    from finlink.ledger_sync import write_ledger_rows
    from finlink.models import CashEntry, PositionRow
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    if ws.ledger:
        raise click.ClickException(
            "ledger.md already has rows — seed-ledger refuses to run non-idempotently. "
            "If you intend to re-seed, clear ledger.md manually first."
        )

    pfile = root / "portfolio" / "positions.md"
    if not pfile.exists():
        raise click.ClickException("no positions.md found — nothing to seed")
    text = pfile.read_text(encoding="utf-8")
    rows = parse_table(text)
    if not rows:
        raise click.ClickException("no positions found in positions.md — nothing to seed")

    ledger_rows: list[dict[str, str]] = []
    for r in rows:
        pos = PositionRow.model_validate(
            {k: v for k, v in r.items() if v not in ("", "-", "---")}
        )
        day = pos.opened_at or date_cls.today()
        ledger_rows.append(
            {
                "date": day.isoformat(),
                "ticker": pos.ticker,
                "side": "buy",
                "quantity": str(pos.quantity),
                "price": str(pos.avg_cost),
                "currency": pos.currency,
                "fees": "0",
                "reason": pos.notes,
                "thesis_slug": pos.thesis_slug or "",
                "fx_rate_usd_at_trade": "",
            }
        )

    # Preserve cash balances as the starting point — they are the current state,
    # not derived from any prior ledger. Only add a cash row if --from-cash and
    # cash.md exists and has rows.
    cash_rows: list[dict[str, str]] = []
    cfile = root / "portfolio" / "cash.md"
    if from_cash and cfile.exists():
        for c in parse_table(cfile.read_text(encoding="utf-8")):
            entry = CashEntry.model_validate(c)
            cash_rows.append({"currency": entry.currency, "amount": str(entry.amount)})

    write_ledger_rows(root, ledger_rows)
    if from_cash and cash_rows:
        from finlink.ledger_sync import write_cash

        write_cash(root, cash_rows)
    click.echo(f"seeded {len(ledger_rows)} opening position row(s) into ledger.md")
    if from_cash:
        click.echo("cash.md left as-is (current state, not history)")

    if not no_commit:
        sha = commit(root, f"finlink seed-ledger ({len(ledger_rows)} rows)")
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
    """Resolve market-data + news drivers. Alpha Vantage is the default.

    `mock` runs fully offline. Any other value (e.g. the LLM driver name, or an
    unknown override) falls back to Alpha Vantage rather than failing at import.
    """
    from finlink.config import RuntimeConfig
    from finlink.ingest.alphavantage_driver import AlphaVantageDriver
    from finlink.ingest.mock import MockMarketDriver, MockNewsDriver
    from finlink.ingest.rss_news_driver import RSSNewsDriver

    cfg = RuntimeConfig.load(root)
    name = (driver_override or cfg.driver or "alphavantage").lower()
    if name == "mock":
        # strict: unknown tickers fail instead of silently inventing prices
        return MockMarketDriver(strict=True), MockNewsDriver()
    if name == "alphavantage":
        try:
            keys = [k for k in (cfg.alphavantage_api_key, cfg.alphavantage_api_key_2) if k]
            return AlphaVantageDriver(keys), RSSNewsDriver()
        except Exception as e:  # noqa: BLE001 - surface missing-key cleanly
            raise click.ClickException(str(e)) from e
    # openrouter / echo / openai etc. are LLM driver names; for market data they
    # all resolve to the default Alpha Vantage driver.
    try:
        keys = [k for k in (cfg.alphavantage_api_key, cfg.alphavantage_api_key_2) if k]
        return AlphaVantageDriver(keys), RSSNewsDriver()
    except Exception as e:  # noqa: BLE001 - surface missing-key cleanly
        raise click.ClickException(str(e)) from e


@main.command()
@click.argument("tickers", nargs=-1)
@click.option("--driver", default=None, help="alphavantage (default) or mock (offline)")
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


@main.command()
@click.option("--thesis", "thesis_slug", default=None, help="Validate one thesis by slug")
@click.option("--ticker", default=None)
@click.option("--driver", default=None, help="openrouter (default) or echo (offline)")
@click.option("--min-score", default=0.10, show_default=True)
@click.option("--no-commit", is_flag=True)
@click.option("--dry-run", is_flag=True, help="Compute and print, but write nothing")
def validate(
    thesis_slug: str | None = None,
    ticker: str | None = None,
    driver: str | None = None,
    min_score: float = 0.10,
    no_commit: bool = False,
    dry_run: bool = False,
) -> None:
    """Re-test theses against new evidence: supporting AND contrary, then a verdict."""
    from datetime import date

    from finlink.domain.context import NoContext, build_context
    from finlink.gitutil import commit
    from finlink.ingest.store import Store
    from finlink.llm.pipelines.validate import ValidateInput
    from finlink.llm.pipelines.validate import validate as validate_run
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    store = Store(root)

    targets = [
        (path, tf)
        for path, tf in ws.theses
        if tf.status.value in ("active", "challenged")
        and (not thesis_slug or tf.slug == thesis_slug)
        and (not ticker or tf.ticker.upper() == ticker.upper())
    ]
    if not targets:
        raise click.ClickException(
            "no active theses to validate"
            + (f" matching {thesis_slug!r}" if thesis_slug else "")
            + " (drafts must be confirmed first: `finlink confirm <file>`)"
        )

    # One portfolio view for the whole run: every validation quotes the same numbers.
    view, _dd, alerts = _portfolio_view(ws, store)
    if view is None:
        click.echo(
            "warning: no cached prices for all holdings — portfolio context will be "
            "reported as unavailable (run `finlink ingest`)",
            err=True,
        )

    try:
        client, cfg = _get_driver(root, driver)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    echo = (driver or cfg.driver or "").lower() == "echo"

    today = date.today()
    for path, tf in targets:
        news = store.load_news(tf.ticker)
        context = build_context(view, tf.ticker, alerts=alerts) if view is not None else NoContext()
        inp = ValidateInput(
            thesis_path=path,
            ticker=tf.ticker,
            hypotheses=[h.model_dump(mode="json") for h in tf.hypotheses],
            created=tf.created,
            horizon=tf.horizon,
            news=news,
            as_of=today,
            context=context,
        )
        try:
            result = validate_run(
                inp,
                client=client,
                model_a="echo" if echo else cfg.model_for("P3_validate_pass_a"),
                model_b="echo" if echo else cfg.model_for("P3_validate_pass_b"),
                model_s="echo" if echo else cfg.model_for("P3_validate_synthesis"),
                min_score=min_score,
                write=not dry_run,
            )
        except RuntimeError as e:
            raise click.ClickException(f"{path.name}: {e}") from e

        click.echo(f"\n=== {path.name} ===")
        click.echo(result.section)
        if result.expired_ids:
            click.echo(f"expired (horizon passed): {', '.join(result.expired_ids)}")
        if dry_run:
            click.echo("[dry-run: nothing written]")

    if not dry_run and not no_commit:
        sha = commit(root, f"finlink validate {thesis_slug or 'all'}")
        if sha:
            click.echo(f"\ncommitted {sha}")


@main.command("status")
def status_cmd() -> None:
    """One-line status per thesis: state, confidence, last validated, horizon."""
    from datetime import date

    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    if not ws.theses:
        click.echo("no theses yet — record a trade to draft one")
        return
    today = date.today()
    rows = []
    for _path, tf in ws.theses:
        horizon = tf.horizon or "-"
        overdue = ""
        if tf.horizon and tf.created:
            from finlink.domain.horizon import horizon_end

            end = horizon_end(tf.created, tf.horizon)
            if end and today > end:
                overdue = f" (horizon ended {end.isoformat()})"
        last = tf.model_dump().get("last_validated") or "-"
        rows.append(
            {
                "thesis": f"{tf.ticker}-{tf.slug}",
                "status": tf.status.value,
                "confidence": tf.confidence.value,
                "created": tf.created.isoformat(),
                "horizon": horizon + overdue,
                "last_validated": str(last),
                "hypotheses": str(len(tf.hypotheses)),
            }
        )
    from finlink.io.markdown import render_table

    click.echo(
        render_table(
            rows,
            [
                "thesis",
                "status",
                "confidence",
                "created",
                "horizon",
                "last_validated",
                "hypotheses",
            ],
        )
    )
    counts: dict[str, int] = {}
    for _, tf in ws.theses:
        counts[tf.status.value] = counts.get(tf.status.value, 0) + 1
    click.echo("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def _alerts_path(root: Path) -> Path:
    return root / "alerts.md"


def _load_alert_records(root: Path) -> list[AlertRecord]:
    from finlink.io.markdown import parse_table

    path = _alerts_path(root)
    if not path.exists():
        return []
    from finlink.domain.alerts import AlertRecord
    from finlink.domain.money import d
    from finlink.domain.risk import Alert, RuleKind

    out: list[AlertRecord] = []
    for row in parse_table(path.read_text(encoding="utf-8")):
        rule_id = row.get("rule", "")
        scope = row.get("scope", "")
        raised = date_cls.fromisoformat(row["raised"])
        alert = Alert(
            rule_id=rule_id,
            kind=RuleKind(row.get("kind") or _rule_kind_for(root, rule_id)),
            scope=scope,
            value_pct=d(row.get("observed") or "0"),
            limit_pct=d(row.get("limit") or "0"),
            message=row.get("message", ""),
        )
        acked = row.get("acked", "-")
        out.append(
            AlertRecord(
                alert=alert,
                raised=raised,
                status=row.get("status", "open"),
                acked=None if acked in ("", "-") else date_cls.fromisoformat(acked),
            )
        )
    return out


def _rule_kind_for(root: Path, rule_id: str) -> str:
    from finlink.workspace import Config

    for raw in Config.load(root).risk_rules:
        if str(raw.get("id") or raw.get("kind")) == rule_id:
            return str(raw.get("kind") or "")
    return "max_position_weight"


def _write_alerts(root: Path, records: list[AlertRecord]) -> None:
    from finlink.domain.alerts import HEADERS, to_rows
    from finlink.io.markdown import render_table

    header = (
        "# Alerts\n\n"
        "Generated by `finlink risk-check` from `domain/risk.py`.\n"
        "Alerts are acknowledged, never silently cleared, and no model can close one.\n\n"
    )
    body = render_table(to_rows(records), HEADERS) + "\n"
    _alerts_path(root).write_text(header + body, encoding="utf-8")


@main.command("risk-check")
@click.option("--no-commit", is_flag=True)
def risk_check(no_commit: bool = False) -> None:
    """Evaluate deterministic risk rules and regenerate alerts.md."""
    from finlink.domain.alerts import reconcile
    from finlink.gitutil import commit
    from finlink.ingest.store import Store
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    view, _dd, alerts = _portfolio_view(ws, Store(root))
    if view is None:
        raise click.ClickException(
            "cannot value the portfolio — run `finlink ingest` for every held ticker"
        )

    today = date_cls.today()
    previous = _load_alert_records(root)
    records = reconcile(previous, alerts, today)
    _write_alerts(root, records)

    if not records:
        click.echo("no alerts — every rule is inside its limit")
    for r in records:
        click.echo(
            f"[{r.status:<12}] {r.alert.rule_id} {r.alert.scope}: "
            f"{r.observed:.2f}% vs limit {r.limit:.2f}% — {r.alert.message}"
        )
    click.echo(f"\nwrote {_alerts_path(root).name}")
    if not no_commit:
        sha = commit(root, "finlink risk-check")
        if sha:
            click.echo(f"committed {sha}")


@main.command()
@click.option("--ack", "ack_key", default=None, help="Acknowledge an alert by rule:scope")
@click.option("--no-commit", is_flag=True)
def alerts(ack_key: str | None = None, no_commit: bool = False) -> None:
    """Show the alert ledger, or acknowledge one (`--ack concentration:NVDA`)."""
    from finlink.domain.alerts import acknowledge
    from finlink.gitutil import commit
    from finlink.io.markdown import render_table

    root = find_root()
    records = _load_alert_records(root)
    if ack_key:
        try:
            records = acknowledge(records, ack_key, date_cls.today())
        except KeyError as e:
            raise click.ClickException(str(e)) from e
        _write_alerts(root, records)
        click.echo(f"acknowledged {ack_key} (record kept; it is never deleted)")
        if not no_commit:
            sha = commit(root, f"finlink alerts --ack {ack_key}")
            if sha:
                click.echo(f"committed {sha}")
        return
    if not records:
        click.echo("no alerts on file — run `finlink risk-check`")
        return
    from finlink.domain.alerts import HEADERS, to_rows

    click.echo(render_table(to_rows(records), HEADERS))


@main.command()
@click.option("--week", default=None, help="ISO week, e.g. 2026-W37; defaults to this week")
@click.option("--driver", default=None, help="openrouter (default) or echo (offline)")
@click.option("--publish", is_flag=True, help="Also write a page into ~/knowledge-base/Wiki/")
@click.option("--no-commit", is_flag=True)
def review(
    week: str | None = None,
    driver: str | None = None,
    publish: bool = False,
    no_commit: bool = False,
) -> None:
    """Generate the weekly review: individual decisions AND portfolio changes."""
    from finlink.domain.alerts import open_alerts
    from finlink.domain.drift import (
        detect_patterns,
        snapshot,
    )
    from finlink.domain.quant import simple_return
    from finlink.domain.usage import compute
    from finlink.gitutil import commit
    from finlink.ingest.store import Store
    from finlink.llm.pipelines.review import ReviewInput, resolve_week
    from finlink.llm.pipelines.review import run as review_run
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    store = Store(root)
    resolved, start, end = resolve_week(week)

    period_trades = [r for r in ws.ledger if start <= r.date <= end]
    patterns = detect_patterns(ws.ledger, as_of=end)

    view, _drawdowns, alerts = _portfolio_view(ws, store)
    start_snap = end_snap = None
    ret_pct = dd_pct = None
    if view is not None:
        rates = build_rate_map(ws)
        start_prices, end_prices = {}, {}
        for tkr in view.positions:
            bars = store.load_prices(tkr.ticker)
            if not bars:
                continue
            end_prices[tkr.ticker] = bars[-1].close
            earlier = [b for b in bars if b.day <= start] or bars[:1]
            start_prices[tkr.ticker] = earlier[-1].close
        start_snap = snapshot(
            ws.rebuild_positions() if ws.ledger else {},
            start_prices,
            ws.cash_by_currency(),
            rates,
            start,
        )
        end_snap = snapshot(
            ws.rebuild_positions() if ws.ledger else {},
            end_prices,
            ws.cash_by_currency(),
            rates,
            end,
        )
        dd_pct = max(_drawdowns.values(), default=None) if _drawdowns else None
        returns = []
        for tkr in view.positions:
            bars = store.load_prices(tkr.ticker)
            window = [b for b in bars if start <= b.day <= end]
            if len(window) >= 2:
                returns.append((simple_return(window), tkr.weight_pct))
        if returns:
            total_weight = sum((w for _r, w in returns), Decimal("0"))
            if total_weight:
                ret_pct = sum((r * w for r, w in returns), Decimal("0")) / total_weight

    conflicts = find_conflicts(ws, view, alerts)

    inp = ReviewInput(
        week=resolved,
        start=start,
        end=end,
        ledger=ws.ledger,
        period_trades=period_trades,
        theses=ws.theses,
        start_snapshot=start_snap,
        end_snapshot=end_snap,
        alerts=_load_alert_records(root),
        conflicts=conflicts,
        patterns=patterns,
        usage=compute(ws.ledger, ws.theses, as_of=end),
        portfolio_return_pct=ret_pct,
        portfolio_drawdown_pct=dd_pct,
    )

    try:
        client, cfg = _get_driver(root, driver)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    echo = (driver or cfg.driver or "").lower() == "echo"
    try:
        result = review_run(
            inp,
            client=client,
            model="echo" if echo else cfg.model_for("P4_review"),
            reviews_dir=root / "reviews",
        )
    except RuntimeError as e:
        raise click.ClickException(f"review failed: {e}") from e

    click.echo(result.body)
    click.echo(f"\nwrote {result.path}")
    if open_alerts(_load_alert_records(root)):
        click.echo("open alerts remain — see `finlink alerts`")

    if publish:
        target = publish_page(result)
        click.echo(f"published -> {target}")
    if not no_commit:
        sha = commit(root, f"finlink review {resolved}")
        if sha:
            click.echo(f"committed {sha}")


def build_rate_map(ws: LoadedWorkspace) -> dict[str, Decimal]:
    from finlink.domain.portfolio import build_rates

    return build_rates(ws.config.fx)


def find_conflicts(
    ws: LoadedWorkspace, view: PortfolioView | None, alerts: list[Alert]
) -> list[str]:
    """A thesis can be intact and still be a bad size — that is the conflict.

    This is the portfolio half's most valuable output: it catches the case where
    validation says 'still valid' and the risk engine says 'over the limit'.
    """
    out: list[str] = []
    if view is None:
        return out
    for alert in alerts:
        for path, tf in ws.theses:
            if tf.ticker.upper() != alert.scope.upper():
                continue
            if tf.status.value not in ("active", "challenged"):
                continue
            out.append(
                f"{tf.ticker} ({path.name}): status {tf.status.value} on a position that "
                f"breaches {alert.rule_id} — {alert.value_pct:.2f}% vs "
                f"{alert.limit_pct:.2f}% limit. Validity is not a reason to add."
            )
    return out


def publish_page(result: ReviewResult) -> Path:
    """Write a synthesized page into the personal knowledge vault.

    Only ever writes this one file; the vault is otherwise treated as read-only.
    """
    from finlink.io.markdown import Doc, render_document

    vault = Path.home() / "knowledge-base" / "Wiki"
    vault.mkdir(parents=True, exist_ok=True)
    target = vault / f"fin-link-review-{result.path.stem}.md"
    fm: dict[str, object] = {
        "title": f"fin-link review — {result.path.stem}",
        "source": "fin-link",
        "type": "review",
        "generated": date_cls.today().isoformat(),
    }
    target.write_text(
        render_document(Doc(frontmatter=fm, body=result.body, path=target)),
        encoding="utf-8",
    )
    return target


def _config_map(root: Path, key: str) -> dict[str, str]:
    """Read a ticker -> label mapping (sectors/countries) from config.yaml."""
    import yaml

    path = root / "config" / "config.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k).upper(): str(v) for k, v in (raw.get(key) or {}).items()}


def _sector_maps(ws: LoadedWorkspace) -> tuple[dict[str, str], dict[str, str]]:
    return _config_map(ws.root, "sectors"), _config_map(ws.root, "countries")


@main.command()
@click.option("--as-of", "as_of", default=None)
def exposure(as_of: str | None = None) -> None:
    """Sector, country and currency exposure + concentration. All figures computed."""
    from finlink.domain.exposure import compute
    from finlink.domain.exposure import render as render_exposure
    from finlink.ingest.store import Store

    root = find_root()
    ws = load_workspace(root)
    view, _dd, _alerts = _portfolio_view(ws, Store(root))
    if view is None:
        raise click.ClickException(
            "cannot value the portfolio — run `finlink ingest` for every held ticker"
        )
    sectors, countries = _sector_maps(ws)
    report = compute(
        view, sectors=sectors, countries=countries, as_of=as_of or date_cls.today().isoformat()
    )
    click.echo(render_exposure(report))
    if report.unclassified_count:
        click.echo(
            f"tip: add {report.unclassified_count} ticker(s) to `sectors:` in "
            "config/config.yaml to classify them"
        )


@main.command()
@click.option("--no-commit", is_flag=True)
def snapshot(no_commit: bool = False) -> None:
    """Record today's portfolio snapshot to data/snapshots (idempotent per day)."""
    from finlink.domain.exposure import compute
    from finlink.domain.snapshots import Snapshot, SnapshotStore
    from finlink.domain.stats import (
        correlation_matrix,
        portfolio_return_series,
        portfolio_volatility,
    )
    from finlink.gitutil import commit
    from finlink.ingest.store import Store

    root = find_root()
    ws = load_workspace(root)
    store = Store(root)
    view, _dd, _alerts = _portfolio_view(ws, store)
    if view is None:
        raise click.ClickException(
            "cannot value the portfolio — run `finlink ingest` for every held ticker"
        )
    sectors, countries = _sector_maps(ws)
    report = compute(view, sectors=sectors, countries=countries)
    today = date_cls.today()

    vol = None
    series = {p.ticker: store.load_prices(p.ticker) for p in view.positions}
    series = {t: b for t, b in series.items() if b}
    if series:
        weights = {p.ticker: p.market_value_usd / view.positions_value_usd for p in view.positions}
        vol = portfolio_volatility(portfolio_return_series(series, weights))
        _ = correlation_matrix(series)  # computed to validate the series aligns

    snap = Snapshot(
        day=today,
        total_usd=view.total_value_usd,
        positions_usd=view.positions_value_usd,
        cash_usd=view.cash_usd,
        cash_pct=view.cash_pct,
        weights={p.ticker: p.weight_pct for p in view.positions},
        sector_weights={b.name: b.weight_pct for b in report.sectors},
        country_weights={b.name: b.weight_pct for b in report.countries},
        currency_weights={b.name: b.weight_pct for b in report.currencies},
        hhi=report.concentration_hhi,
        volatility_pct=vol,
    )
    SnapshotStore(root).append(snap)
    click.echo(
        f"snapshot {today.isoformat()}: total {snap.total_usd:.2f} USD, "
        f"HHI {snap.hhi:.4f}, cash {snap.cash_pct:.2f}%"
        + (f", vol {vol:.2f}%" if vol is not None else "")
    )
    if not no_commit:
        sha = commit(root, f"finlink snapshot {today.isoformat()}")
        if sha:
            click.echo(f"committed {sha}")


@main.command("report")
@click.option("--out", "out_path", default=None, help="Output HTML path")
@click.option("--with-correlation", is_flag=True, help="Include the correlation matrix")
def report_cmd(out_path: str | None = None, with_correlation: bool = False) -> None:
    """Render a self-contained static HTML report (inline SVG, no server)."""
    from finlink.domain.exposure import compute
    from finlink.domain.snapshots import SnapshotStore
    from finlink.domain.stats import correlation_matrix
    from finlink.ingest.store import Store
    from finlink.io.markdown import parse_table
    from finlink.report.html import render as render_html
    from finlink.report.html import write as write_html

    root = find_root()
    ws = load_workspace(root)
    store = Store(root)
    view, _dd, _alerts = _portfolio_view(ws, store)
    if view is None:
        raise click.ClickException(
            "cannot value the portfolio — run `finlink ingest` for every held ticker"
        )
    sectors, countries = _sector_maps(ws)
    exposure_report = compute(view, sectors=sectors, countries=countries)

    corr_rows = None
    corr_header = None
    if with_correlation:
        series = {p.ticker: store.load_prices(p.ticker) for p in view.positions}
        series = {t: b for t, b in series.items() if b}
        matrix = correlation_matrix(series)
        if matrix:
            names = sorted({c.a for c in matrix} | {c.b for c in matrix})
            corr_header = [""] + names
            lookup = {(c.a, c.b): c for c in matrix}
            corr_rows = []
            for a in names:
                cells = []
                for b in names:
                    if a == b:
                        cells.append("1.00")
                        continue
                    c = lookup.get((a, b)) or lookup.get((b, a))
                    cells.append(f"{c.coefficient:.2f}" if c and c.reliable else "n/a")
                corr_rows.append((a, cells))

    alert_rows: list[tuple[str, str, str]] = []
    alerts_path = root / "alerts.md"
    if alerts_path.exists():
        for row in parse_table(alerts_path.read_text(encoding="utf-8")):
            if row.get("status") == "open":
                alert_rows.append(
                    (
                        row.get("rule", ""),
                        row.get("scope", ""),
                        f"{row.get('observed', '')}% vs {row.get('limit', '')}%",
                    )
                )

    html = render_html(
        exposure=exposure_report,
        snapshots=SnapshotStore(root).load(),
        correlation_rows=corr_rows,
        correlation_header=corr_header,
        alerts=alert_rows,
        title=f"fin-link portfolio report — {date_cls.today().isoformat()}",
    )
    target = Path(out_path) if out_path else root / "reports" / "portfolio.html"
    write_html(target, html)
    click.echo(f"wrote {target}")
    click.echo("open it directly in a browser — no server needed")


def _positions_from_workspace(ws: LoadedWorkspace) -> dict[str, Position]:
    """Holdings from the ledger if there are trades, else from positions.md.

    A hand-seeded workspace has no ledger rows yet; without this fallback every
    portfolio command (exposure, risk-check, snapshot) reports nothing at all.
    """
    if ws.ledger:
        return ws.rebuild_positions()
    out: dict[str, Position] = {}
    for row in ws.positions:
        pos = out.setdefault(row.ticker, Position(ticker=row.ticker, currency=row.currency))
        pos.add_lot(
            Lot(
                quantity=row.quantity,
                unit_cost=row.avg_cost,
                currency=row.currency,
                opened_at=row.opened_at.isoformat() if row.opened_at else "",
            )
        )
    return out


def _portfolio_view(
    ws: LoadedWorkspace, store: Store
) -> tuple[PortfolioView | None, dict[str, Decimal], list[Alert]]:
    """Shared valuation used by risk-check, validate, review, exposure and snapshot.

    Returns (view_or_None, drawdowns, alerts). A failure here is advisory: a missing
    price must not block a thesis review, only the portfolio half of it.
    """
    from finlink.domain.portfolio import value_positions
    from finlink.domain.quant import max_drawdown
    from finlink.domain.risk import evaluate, parse_rules

    positions = _positions_from_workspace(ws)
    held = sorted(positions)
    prices: dict[str, Decimal] = {}
    for tkr in held:
        price = store.latest_price(tkr)
        if price is not None:
            prices[tkr] = price
    if not positions or len(prices) != len(held):
        return None, {}, []
    view = value_positions(positions, prices, ws.cash_by_currency(), ws.config.fx)
    drawdowns: dict[str, Decimal] = {}
    for tkr in held:
        bars = store.load_prices(tkr)
        if bars:
            drawdowns[tkr] = max_drawdown(bars)
    alerts = evaluate(view, parse_rules(ws.config.risk_rules), drawdowns=drawdowns)
    return view, drawdowns, list(alerts)


def _date_hint(value: str) -> str | None:
    """Return a human hint if `value` is not an ISO date, else None."""
    from datetime import date as _date

    try:
        _date.fromisoformat(value)
        return None
    except ValueError:
        pass
    if "." in value:
        return f"is not a date — use dashes: {value.replace('.', '-')}"
    if len(value) == 7 and value[4] == "-":
        return f"is not a date — add a day: {value}-01"
    return "is not a date — expected YYYY-MM-DD"


@main.command("check")
@click.option(
    "--fix-separator",
    is_flag=True,
    help="Repair a missing/malformed table separator row in positions.md",
)
def check_cmd(fix_separator: bool = False) -> None:
    """Validate hand-edited portfolio files with actionable, row-level errors.

    Run this BEFORE `doctor` after editing positions.md / cash.md by hand. Same
    strictness, but the messages name the exact cell and what to write instead.
    """
    from finlink.io.markdown import MarkdownError, parse_table

    root = find_root()
    errors: list[str] = []
    warnings: list[str] = []
    fixed: list[str] = []

    for name, required in (
        (
            "portfolio/positions.md",
            [
                "ticker",
                "quantity",
                "avg_cost",
                "currency",
                "opened_at",
                "thesis_slug",
                "notes",
            ],
        ),
        ("portfolio/cash.md", ["currency", "amount"]),
    ):
        path = root / name
        if not path.exists():
            errors.append(f"MISSING {name}")
            continue
        text = path.read_text(encoding="utf-8")

        # A malformed separator row silently drops the first holding.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 2:
            sep = lines[1].strip()
            ok_sep = sep.startswith("|") and set(
                sep.replace("|", "").replace(":", "").replace("-", "").strip()
            ) <= set(" ")
            if not ok_sep:
                msg = (
                    f"{name}: line 2 is not a table separator (got {sep[:40]!r}) — "
                    "the parser is dropping the first holding"
                )
                if fix_separator:
                    n = len([c for c in lines[0].strip().strip("|").split("|")])
                    lines[1] = "| " + " | ".join(["---"] * n) + " |"
                    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    fixed.append(f"{name}: inserted separator row ({n} columns)")
                else:
                    errors.append(msg + ". Re-run with --fix-separator to repair it.")

        try:
            rows = parse_table(text)
        except MarkdownError as exc:
            errors.append(f"{name}: {exc}")
            continue

        for i, row in enumerate(rows, start=1):
            for col in required:
                if col not in row:
                    errors.append(f"{name} row {i}: missing column {col!r}")
            if name.endswith("positions.md"):
                ticker = row.get("ticker", "")
                if ticker and ticker != ticker.strip():
                    errors.append(f"{name} row {i}: ticker has stray whitespace {ticker!r}")
                if ticker and " " in ticker.strip():
                    warnings.append(
                        f"{name} row {i}: ticker {ticker!r} contains a space — "
                        "use a real symbol e.g. 'VOLV-B.ST', not 'Lundin Gold'"
                    )
                if ticker and not ticker.strip().isupper() and ticker.strip().isalpha():
                    warnings.append(
                        f"{name} row {i}: ticker {ticker!r} should be uppercase "
                        f"({ticker.strip().upper()!r})"
                    )
                ccy = row.get("currency", "").strip().upper()
                if ccy and ccy not in ("USD", "HKD", "SEK"):
                    errors.append(
                        f"{name} row {i}: currency {ccy!r} unsupported — use USD, HKD or SEK"
                    )
                opened = row.get("opened_at", "").strip()
                if opened and opened not in ("-", ""):
                    hint = _date_hint(opened)
                    if hint is not None:
                        errors.append(f"{name} row {i}: opened_at {opened!r} {hint}")
                for num_col in ("quantity", "avg_cost"):
                    raw = row.get(num_col, "").strip()
                    if not raw or raw == "-":
                        errors.append(f"{name} row {i}: {num_col} is empty")
                        continue
                    try:
                        Decimal(raw)
                    except Exception:
                        errors.append(f"{name} row {i}: {num_col} {raw!r} is not a number")
                    if raw != raw.strip():
                        errors.append(f"{name} row {i}: {num_col} has stray whitespace")
            else:
                ccy = row.get("currency", "").strip().upper()
                if ccy and ccy not in ("USD", "HKD", "SEK"):
                    errors.append(
                        f"{name} row {i}: currency {ccy!r} unsupported — use USD, HKD or SEK"
                    )
                amount = row.get("amount", "").strip()
                try:
                    Decimal(amount)
                except Exception:
                    errors.append(f"{name} row {i}: amount {amount!r} is not a number")

    warnings += _config_coverage_warnings(root)
    for f in fixed:
        click.echo(f"FIXED  {f}")
    for w in warnings:
        click.echo(f"WARN   {w}")
    if errors:
        click.echo("")
        for e in errors:
            click.echo(f"ERROR  {e}")
        click.echo(f"\ncheck FAILED: {len(errors)} error(s)")
        raise SystemExit(1)
    click.echo(f"check OK — {root}")
    click.echo("next: `finlink doctor`")


# The three buckets chosen in config/config.yaml. A ticker landing outside them
# means its mapping is missing, not that a new region appeared.
COUNTRY_BUCKETS = ("US", "China", "EU")


def _config_coverage_warnings(root: Path) -> list[str]:
    """Flag holdings the config does not classify, and any unknown country label.

    Classification is never guessed, so a missing mapping shows up as `Unclassified`
    in every exposure report with no error anywhere. This names the key to add.
    """
    from finlink.domain.classify import classify
    from finlink.io.markdown import MarkdownError, parse_table

    sectors, countries = _config_map(root, "sectors"), _config_map(root, "countries")
    try:
        rows = parse_table((root / "portfolio" / "positions.md").read_text(encoding="utf-8"))
    except (OSError, MarkdownError):
        return []

    out: list[str] = []
    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        if not ticker or ticker == "-":
            continue
        ccy = (row.get("currency") or "USD").strip().upper()
        cls = classify(ticker, ccy, sectors=sectors, countries=countries)
        if cls.sector == "Unclassified":
            out.append(
                f"portfolio/positions.md: {ticker} has no sector — add "
                f"`{cls.ticker}: <name>` under `sectors:` in config/config.yaml"
            )
        if cls.country not in COUNTRY_BUCKETS:
            out.append(
                f"portfolio/positions.md: {ticker} resolved to country {cls.country!r}, "
                f"not one of {'/'.join(COUNTRY_BUCKETS)} — add "
                f"`{cls.ticker}: <US|China|EU>` under `countries:` in config/config.yaml"
            )
    return out


@main.command("backfill-theses")
@click.option("--driver", default=None, help="openrouter (default) or echo (offline)")
@click.option("--ticker", "only", default=None, help="Only this ticker")
@click.option("--dry-run", is_flag=True, help="Show what would be created")
@click.option("--no-commit", is_flag=True)
@click.option("--no-link", is_flag=True, help="Do not write thesis_slug back into positions.md")
@click.option("--replace", is_flag=True,
              help="Overwrite existing DRAFT theses for these tickers (never touches active ones)")
def backfill_theses(
    driver: str | None,
    only: str | None,
    dry_run: bool,
    no_commit: bool,
    no_link: bool,
    replace: bool = False,
) -> None:
    """Create one DRAFT thesis per held position from its `notes` reason.

    Bridges a hand-seeded portfolio into the thesis workflow: every holding gets a
    draft thesis (status: draft), and positions.md is linked to it by slug.

    Drafts need `finlink confirm` before `validate` will touch them — the model
    structures your reason, it does not decide whether you were right.
    """
    from finlink.domain.slug import slugify, unique_slug
    from finlink.gitutil import commit
    from finlink.llm.pipelines.decompose import DecomposeInput
    from finlink.llm.pipelines.decompose import run as decompose_run
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)

    # Slugs already used by existing theses, so we never collide with them.
    # With --replace, draft slugs are freed so the regenerated thesis keeps the
    # same name (and therefore the same link) instead of gaining a -2 suffix.
    replaceable = {
        tf.slug
        for _p, tf in ws.theses
        if tf.status.value == "draft"
        and (not only or tf.ticker.upper() == only.upper())
    }
    taken = (
        {tf.slug for _p, tf in ws.theses} - replaceable if replace
        else {tf.slug for _p, tf in ws.theses}
    )
    targets = [
        p
        for p in ws.positions
        if p.notes
        and p.notes.strip()
        and p.notes.strip() != "-"
        and (not only or p.ticker.upper() == only.upper())
    ]
    if not targets:
        raise click.ClickException(
            "no position has a reason in `notes` — add one, or pass --ticker"
        )
    skipped = [
        p.ticker for p in ws.positions if not (p.notes or "").strip() or p.notes.strip() == "-"
    ]
    if skipped:
        click.echo(f"skipped (no reason in notes): {', '.join(skipped)}")

    try:
        client, cfg = _get_driver(root, driver)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    echo = (driver or cfg.driver or "").lower() == "echo"

    created: list[tuple[str, str, Path | None]] = []
    for pos in targets:
        reason = pos.notes.strip()
        slug = unique_slug(slugify(reason), taken)
        taken.add(slug)
        target = root / "theses" / f"{pos.ticker}-{slug}.md"
        if target.exists():
            if not replace:
                click.echo(f"skip {pos.ticker}: {target.name} exists (use --replace)")
                continue
            _ensure_draft(target, f"{pos.ticker}: refusing to overwrite")
            if not dry_run:
                target.unlink()
        if dry_run:
            created.append((pos.ticker, slug, None))
            click.echo(f"would create {pos.ticker}-{slug}.md  (reason: {reason[:60]})")
            continue
        try:
            result = decompose_run(
                DecomposeInput(
                    ticker=pos.ticker,
                    reason=reason,
                    horizon="",
                    slug=slug,
                    as_of=pos.opened_at or date_cls.today(),
                ),
                client=client,
                theses_dir=root / "theses",
                model="echo" if echo else cfg.model_for("P1_decompose"),
            )
        except FileExistsError as e:
            click.echo(f"skip {pos.ticker}: {e}")
            continue
        except RuntimeError as e:
            raise click.ClickException(f"{pos.ticker}: {e}") from e
        created.append((pos.ticker, slug, result.path))
        click.echo(f"created {result.path.name}")

    if dry_run:
        click.echo("\n[dry-run: nothing written]")
        return

    if not no_link and created:
        _link_slugs(root, {t: s for t, s, _p in created})

    click.echo(
        f"\n{len(created)} draft thesis(es). Review each, then confirm with:\n"
        + "\n".join(f"  finlink confirm theses/{t}-{s}.md" for t, s, _p in created)
    )
    if not no_commit:
        sha = commit(root, f"finlink backfill-theses ({len(created)})")
        if sha:
            click.echo(f"committed {sha}")


@main.command("improve-slugs")
@click.option("--driver", default=None, help="openrouter (default) or echo (offline)")
@click.option("--ticker", "only", default=None, help="Only this ticker")
@click.option("--only-unreadable", is_flag=True, help="Only rename machine-derived slugs")
@click.option("--dry-run", is_flag=True, help="Show proposed names; write nothing")
@click.option("--no-commit", is_flag=True)
def improve_slugs(
    driver: str | None,
    only: str | None,
    only_unreadable: bool,
    dry_run: bool,
    no_commit: bool,
) -> None:
    """Ask a model for better thesis NAMES, then rename file + links.

    The model proposes the words; `domain/slug.py` derives the slug. That is why
    this is safe to run: a filename is also a link target in positions.md, so it
    must stay deterministic and path-safe even though the wording is chosen by a
    model. A suggestion that would not survive that rule is rejected, not applied.

    Run it after seeding a portfolio, or after recording reasons in Chinese or
    another non-Latin script — the deterministic slug for those is a codepoint
    token (`cjk-770b597d817e`) that carries no meaning.
    """
    from finlink.domain.slug import is_adoptable, unique_slug
    from finlink.gitutil import commit
    from finlink.llm.pipelines.slug import SlugInput
    from finlink.llm.pipelines.slug import run as slug_run
    from finlink.workspace import load_workspace

    root = find_root()
    ws = load_workspace(root)
    cfg = RuntimeConfig.load(root)

    targets = [
        (path, tf)
        for path, tf in ws.theses
        if (not only or tf.ticker.upper() == only.upper())
        and (not only_unreadable or not _is_readable(tf.slug))
    ]
    if not targets:
        raise click.ClickException(
            "no theses to rename"
            + (f" for {only!r}" if only else "")
            + (" (every slug is already readable)" if only_unreadable else "")
        )

    try:
        client, cfg = _get_driver(root, driver)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    echo = (driver or cfg.driver or "").lower() == "echo"

    # The reason lives in the thesis body; fall back to the notes column so a
    # hand-seeded position without a thesis file still has something to name.
    notes_by_ticker = {p.ticker.upper(): p.notes for p in ws.positions}
    taken = {tf.slug for _p, tf in ws.theses}
    renamed: list[tuple[Path, Path, str, str]] = []

    for path, tf in targets:
        reason = _thesis_reason(path) or notes_by_ticker.get(tf.ticker.upper(), "")
        if not reason.strip():
            click.echo(f"skip {path.name}: no reason text to name it from")
            continue
        try:
            result = slug_run(
                SlugInput(ticker=tf.ticker, reason=reason, current_slug=tf.slug),
                client=client,
                model="echo" if echo else cfg.model_for("P6_slug"),
            )
        except RuntimeError as e:
            click.echo(f"skip {path.name}: {e}", err=True)
            continue
        if not result.adopted:
            click.echo(f"keep  {path.name}: {result.reason}")
            continue
        if not is_adoptable(result.slug):
            click.echo(f"skip  {path.name}: rejected unsafe slug {result.slug!r}")
            continue
        # Never rename onto a name another thesis already holds — doctor fails on
        # duplicate slugs, and two theses sharing one is worse than a bad name.
        new_slug = unique_slug(result.slug, taken - {tf.slug})
        taken.add(new_slug)
        target = root / "theses" / f"{tf.ticker}-{new_slug}.md"
        click.echo(f"{'would rename' if dry_run else 'rename'} {path.name} -> {target.name}")
        if not dry_run:
            _rename_thesis(root, path, target, tf.slug, new_slug)
            renamed.append((path, target, tf.slug, new_slug))

    if dry_run:
        click.echo("\n[dry-run: nothing written]")
        return
    if not renamed:
        click.echo("\nno slugs changed")
        return
    click.echo(
        f"\nrenamed {len(renamed)} thesis file(s) and updated every link to them.\n"
        "Review with `git diff`, or undo with `git checkout -- .`"
    )
    if not no_commit:
        sha = commit(root, f"finlink improve-slugs ({len(renamed)})")
        if sha:
            click.echo(f"committed {sha}")


# Deterministic slugs a human cannot read: CJK codepoint tokens and bare hashes.
_UNREADABLE_SLUG = re.compile(r"^(cjk-[0-9a-f]+|thesis-[0-9a-f]{8})$")


def _is_readable(slug: str) -> bool:
    return not _UNREADABLE_SLUG.match(slug or "")


def _thesis_reason(path: Path) -> str:
    """The investor's own words from a thesis file — the `## Thesis` section."""
    from finlink.io.markdown import read_document

    try:
        doc = read_document(path)
    except Exception:  # noqa: BLE001 - an unreadable thesis is skipped, not fatal
        return ""
    section = doc.section("Thesis")
    if section:
        return section.strip()
    for line in doc.body.splitlines():
        if line.strip() and not line.startswith(("#", "-", "_")):
            return line.strip()
    return ""


def _rename_thesis(root: Path, src: Path, dst: Path, old_slug: str, new_slug: str) -> None:
    """Rename a thesis file and repoint every link at it.

    Three things must move together or `doctor` fails: the file, its `slug`
    frontmatter key, and every `thesis_slug` cell that pointed at the old name.
    """
    from finlink.io.markdown import set_frontmatter_key

    set_frontmatter_key(src, "slug", new_slug)
    src.rename(dst)
    _relink_slugs(root, {old_slug: new_slug})


def _relink_slugs(root: Path, mapping: dict[str, str]) -> None:
    """Rewrite `thesis_slug` cells in positions.md and ledger.md.

    Cell-scoped: every other cell and row is preserved byte-for-byte.
    """
    from finlink.io.markdown import parse_table

    for rel in ("portfolio/positions.md", "portfolio/ledger.md"):
        path = root / rel
        if not path.exists():
            continue
        try:
            parse_table(path.read_text(encoding="utf-8"))
        except MarkdownError:
            continue  # unparseable file: doctor reports it; do not silently mangle it
        lines = path.read_text(encoding="utf-8").splitlines()
        header: list[str] = []
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("|") and s.count("|") >= 2:
                cells = [c.strip() for c in s.strip("|").split("|")]
                if not header:
                    header = [c.lower() for c in cells]
                    out.append(ln)
                    continue
                if "thesis_slug" in header and not set(cells[0]) <= set("-: "):
                    idx = header.index("thesis_slug")
                    if idx < len(cells) and cells[idx] in mapping:
                        cells[idx] = mapping[cells[idx]]
                        ln = "| " + " | ".join(cells) + " |"
            out.append(ln)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _ensure_draft(path: Path, context: str) -> None:
    """Refuse to overwrite a thesis the user has already confirmed.

    Drafts are regenerable; an active/challenged thesis holds validated history
    that no backfill may destroy.
    """
    from finlink.io.markdown import read_document

    status = str(read_document(path).frontmatter.get("status", ""))
    if status != "draft":
        raise click.ClickException(
            f"{context} {path.name}: status is {status!r}, not draft. "
            "Refusing to overwrite confirmed work."
        )


def _link_slugs(root: Path, mapping: dict[str, str]) -> None:
    """Write thesis slugs into positions.md AND ledger.md — key-scoped, never a rewrite.

    Only the `thesis_slug` cell changes; every other cell and all other rows are
    preserved byte-for-byte. In the ledger, the slug is written to each BUY row of
    the matching ticker (sells keep their slug).
    """
    for rel in ("portfolio/positions.md", "portfolio/ledger.md"):
        path = root / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        header: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                # Detect the header row by its column names (positions: 'ticker'
                # first; ledger: 'date' first). Both carry a 'thesis_slug' column.
                is_header = not header and (
                    (cells and cells[0].lower() == "ticker")
                    or ("thesis_slug" in [c.lower() for c in cells])
                    or ("date" in [c.lower() for c in cells]
                        and "side" in [c.lower() for c in cells])
                )
                if is_header:
                    header = cells
                    out.append(ln)
                    continue
                if header and not set(cells[0]) <= set("-: "):
                    idx = header.index("thesis_slug") if "thesis_slug" in header else None
                    ticker_col = header.index("ticker") if "ticker" in header else 0
                    ticker = cells[ticker_col]
                    if idx is not None and ticker.upper() in {
                        k.upper(): v for k, v in mapping.items()
                    }:
                        # In the ledger, only buys carry a thesis slug.
                        if rel.endswith("ledger.md"):
                            side_idx = header.index("side") if "side" in header else None
                            if side_idx is None or cells[side_idx].strip().lower() != "buy":
                                out.append(ln)
                                continue
                        want = next(
                            v for k, v in mapping.items() if k.upper() == ticker.upper()
                        )
                        cells[idx] = want
                        ln = "| " + " | ".join(cells) + " |"
            out.append(ln)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
