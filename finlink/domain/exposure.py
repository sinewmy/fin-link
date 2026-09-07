"""Portfolio-level exposure analytics (Phase 5).

Answers what per-stock analysis cannot: how much sits in one sector, one country,
one currency, and how concentrated the whole thing is. Every figure is computed
here; nothing is estimated and nothing comes from a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finlink.domain.classify import Classification, classify, classify_all
from finlink.domain.money import q, to_usd
from finlink.domain.portfolio import PortfolioView
from finlink.domain.stats import Correlation

ZERO = Decimal("0")
ONE = Decimal("1")
PCT = Decimal("0.01")


@dataclass(frozen=True)
class Bucket:
    name: str
    value_usd: Decimal
    weight_pct: Decimal

    @property
    def is_unclassified(self) -> bool:
        return self.name in ("Unclassified", "Unknown")


@dataclass(frozen=True)
class ExposureReport:
    as_of: str
    total_usd: Decimal
    sectors: tuple[Bucket, ...]
    countries: tuple[Bucket, ...]
    currencies: tuple[Bucket, ...]
    buckets: tuple[Bucket, ...]
    classifications: dict[str, Classification]
    concentration_hhi: Decimal
    top_position: Bucket | None
    effective_positions: Decimal
    largest_sector: Bucket | None
    largest_country: Bucket | None
    unclassified_count: int

    @property
    def fx_note(self) -> str:
        """Exit criterion: SEK/HKD exposure must be explicit, not implicit."""
        parts = [f"{c.name} {q(c.weight_pct, PCT)}%" for c in self.currencies]
        return " · ".join(parts)


def _sector_of(classifications: dict[str, Classification], ticker: str) -> str:
    """Look up by raw OR normalised ticker.

    classifications is keyed by NORMALISED ticker (00700 -> 0700.HK), so a raw
    lookup misses every unsuffixed HK code and reports it as Unclassified.
    """
    from finlink.domain.classify import normalise_ticker

    for candidate in (
        ticker,
        ticker.upper(),
        normalise_ticker(ticker, "HKD"),
        normalise_ticker(ticker, "USD"),
    ):
        cls = classifications.get(candidate)
        if cls is not None:
            return cls.sector
    return "Unclassified"


def _buckets(values: dict[str, Decimal], total: Decimal) -> tuple[Bucket, ...]:
    if total == 0:
        return ()
    out = [
        Bucket(name=name, value_usd=value, weight_pct=value / total * 100)
        for name, value in values.items()
    ]
    out.sort(key=lambda b: b.weight_pct, reverse=True)
    return tuple(out)


def compute(
    view: PortfolioView,
    *,
    sectors: dict[str, str] | None = None,
    countries: dict[str, str] | None = None,
    as_of: str = "",
) -> ExposureReport:
    """Build the exposure report from a valued portfolio."""
    tickers = [(p.ticker, p.currency) for p in view.positions]
    classifications = classify_all(tickers, sectors=sectors, countries=countries)

    by_sector: dict[str, Decimal] = {}
    by_country: dict[str, Decimal] = {}
    by_currency: dict[str, Decimal] = {}
    by_bucket: dict[str, Decimal] = {}

    for p in view.positions:
        if p.quantity <= 0:
            continue
        # classify_all keys by NORMALISED ticker (00700 -> 0700.HK), so a raw
        # lookup misses every unsuffixed HK code and raises KeyError.
        cls = classifications.get(p.ticker) or classifications.get(p.ticker.upper())
        if cls is None:
            cls = classify(p.ticker, p.currency, sectors=sectors, countries=countries)
        by_sector[cls.sector] = by_sector.get(cls.sector, ZERO) + p.market_value_usd
        by_country[cls.country] = by_country.get(cls.country, ZERO) + p.market_value_usd
        by_currency[cls.currency] = by_currency.get(cls.currency, ZERO) + p.market_value_usd
        by_bucket[cls.bucket] = by_bucket.get(cls.bucket, ZERO) + p.market_value_usd

    total = view.positions_value_usd
    sector_buckets = _buckets(by_sector, total)
    country_buckets = _buckets(by_country, total)
    currency_buckets = _buckets(by_currency, total)
    bucket_buckets = _buckets(by_bucket, total)

    hhi = sum((p.weight_pct**2 for p in view.positions), ZERO) / Decimal("10000")
    effective = ONE / hhi if hhi > 0 else ZERO

    top = max(view.positions, key=lambda p: p.weight_pct, default=None)
    unclassified = sum(
        1 for p in view.positions if _sector_of(classifications, p.ticker) == "Unclassified"
    )

    return ExposureReport(
        as_of=as_of,
        total_usd=total,
        sectors=sector_buckets,
        countries=country_buckets,
        currencies=currency_buckets,
        buckets=bucket_buckets,
        classifications=classifications,
        concentration_hhi=hhi,
        top_position=(Bucket(top.ticker, top.market_value_usd, top.weight_pct) if top else None),
        effective_positions=effective,
        largest_sector=sector_buckets[0] if sector_buckets else None,
        largest_country=country_buckets[0] if country_buckets else None,
        unclassified_count=unclassified,
    )


def render(
    report: ExposureReport,
    correlations: list[Correlation] | None = None,
    vol: Decimal | None = None,
) -> str:
    """Markdown rendering — written by code, as always."""
    lines: list[str] = ["# Portfolio exposure", ""]
    lines.append(f"Total positions (USD): {q(report.total_usd)}")
    lines.append(f"Concentration (HHI): {q(report.concentration_hhi, Decimal('0.0001'))}")
    lines.append(f"Effective number of positions: {q(report.effective_positions, PCT)}")
    if report.top_position:
        lines.append(
            f"Largest position: {report.top_position.name} "
            f"{q(report.top_position.weight_pct, PCT)}%"
        )
    lines.append("")

    for title, rows in (
        ("Currency exposure (USD-normalised)", report.currencies),
        ("Sector exposure", report.sectors),
        ("Country exposure", report.countries),
        ("Growth vs defensive", report.buckets),
    ):
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("_none_")
            lines.append("")
            continue
        lines.append("| name | value_usd | weight |")
        lines.append("| --- | --- | --- |")
        for row in rows:
            flag = " ⚠" if row.is_unclassified else ""
            lines.append(f"| {row.name}{flag} | {q(row.value_usd)} | {q(row.weight_pct, PCT)}% |")
        lines.append("")

    lines.append(f"FX summary: {report.fx_note}")
    if report.unclassified_count:
        lines.append(
            f"⚠ {report.unclassified_count} position(s) have no sector mapping — "
            "set `sectors:` in config/config.yaml"
        )
    if vol is not None:
        lines.append(f"Portfolio volatility (annualised): {q(vol, PCT)}%")

    if correlations:
        matrix_names = sorted({c.a for c in correlations} | {c.b for c in correlations})
        lines += ["", "## Correlation matrix (daily returns)", ""]
        lines.append("| " + " | ".join([""] + matrix_names) + " |")
        lines.append("| " + " | ".join(["---"] * (len(matrix_names) + 1)) + " |")
        lookup = {(c.a, c.b): c for c in correlations}
        for row_name in matrix_names:
            cells = []
            for col_name in matrix_names:
                if row_name == col_name:
                    cells.append("1.00")
                    continue
                c = lookup.get((row_name, col_name)) or lookup.get((col_name, row_name))
                cells.append(f"{q(c.coefficient, Decimal('0.01'))}" if c and c.reliable else "n/a")
            lines.append("| " + " | ".join([row_name] + cells) + " |")
        unreliable = [c for c in correlations if not c.reliable and c.a != c.b]
        if unreliable:
            lines.append("")
            lines.append(
                f"_{len(unreliable)} pair(s) marked n/a: fewer than "
                f"{MIN_OVERLAP_LABEL} overlapping observations._"
            )
    return "\n".join(lines) + "\n"


MIN_OVERLAP_LABEL = "20"


def cash_by_currency(
    cash: dict[str, Decimal], rates: dict[str, Decimal], total: Decimal
) -> tuple[Bucket, ...]:
    """Cash exposure by currency — part of the FX picture, not an afterthought."""
    if total == 0:
        return ()
    values = {ccy: to_usd(amt, ccy, rates) for ccy, amt in cash.items()}
    return _buckets(values, total)


def positions_to_series_weights(view: PortfolioView) -> dict[str, Decimal]:
    """Weight as a fraction (0-1) for portfolio return maths."""
    total = view.positions_value_usd
    if total == 0:
        return {}
    return {p.ticker: p.market_value_usd / total for p in view.positions}
