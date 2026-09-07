"""Sector, country and currency classification.

Deterministic. Precedence: explicit config mapping -> ticker-suffix inference ->
position currency -> 'Unclassified'/'Unknown'. Never guesses from a company name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Ticker suffix -> market. This is how the user's HK names (0700.HK) and Swedish
# names (VOLV-B.ST) are recognised without a lookup service.
SUFFIX_COUNTRY: dict[str, str] = {
    ".HK": "China",
    ".ST": "EU",
    ".SS": "EU",
    ".TO": "Canada",
    ".L": "United Kingdom",
    ".AX": "Australia",
    ".DE": "Germany",
    ".PA": "France",
    ".T": "Japan",
}

# Bare Hong Kong-style numeric codes (e.g. 00700) with no suffix.
HK_CODE_COUNTRY = "China"

# Kept consistent with the three-bucket scheme the user chose in config
# (US / China / EU) so an unmapped ticker never opens a fourth bucket.
CURRENCY_COUNTRY: dict[str, str] = {
    "USD": "US",
    "HKD": "China",
    "SEK": "EU",
}

GROWTH = "growth"
DEFENSIVE = "defensive"
UNCLASSIFIED_BUCKET = "Cyclical"

# Sector -> growth/defensive. Only what we can defend; the rest is Cyclical.
SECTOR_BUCKET: dict[str, str] = {
    "Information Technology": GROWTH,
    "Communication Services": GROWTH,
    "Consumer Discretionary": GROWTH,
    "Health Care": DEFENSIVE,
    "Consumer Staples": DEFENSIVE,
    "Utilities": DEFENSIVE,
}


@dataclass(frozen=True)
class Classification:
    ticker: str
    sector: str
    country: str
    currency: str
    bucket: str

    @property
    def classified_sector(self) -> bool:
        return self.sector != "Unclassified"


def normalise_ticker(ticker: str, currency: str) -> str:
    """Uppercase and add the exchange suffix implied by the market when missing.

    '00700' with HKD becomes '0700.HK' so suffix-based rules apply uniformly.
    """
    t = ticker.strip().upper()
    if "." in t:
        return t
    if currency.upper() == "HKD" and t.isdigit():
        stripped = t.lstrip("0") or t
        return f"{stripped.zfill(4)}.HK"
    return t


def infer_country(ticker: str, currency: str) -> str:
    t = ticker.strip().upper()
    for suffix, country in SUFFIX_COUNTRY.items():
        if t.endswith(suffix):
            return country
    if t.isdigit():  # unsuffixed HK-style code
        return HK_CODE_COUNTRY
    return CURRENCY_COUNTRY.get(currency.upper(), "Unknown")


def currency_for(ticker: str, position_currency: str) -> str:
    """Cash/position currency, defaulting sensibly for unsuffixed tickers."""
    t = ticker.strip().upper()
    if t.endswith(".HK") or (t.isdigit() and position_currency.upper() == "HKD"):
        return "HKD"
    if t.endswith((".ST", ".SS")):
        return "SEK"
    return position_currency.upper()


def canonical(ticker: str) -> tuple[str, str]:
    """(root, suffix) with leading zeros stripped from the root.

    '00700', '0700', '0700.HK' all canonicalise to ('700', ...) so a config key
    written the way the broker prints it still matches the normalised ticker
    classification works with.
    """
    root, _, suffix = ticker.strip().upper().partition(".")
    return (root.lstrip("0") or root, suffix)


def _lookup(mapping: Mapping[str, str], normalised: str, raw: str) -> str | None:
    """Match a config entry against the raw AND the normalised ticker.

    The user may write a code as it appears in positions.md ('00700'), while
    classification works with the normalised form ('0700.HK'). Both must hit, or the
    holding silently reports as Unclassified/Unknown.
    """
    for candidate in (raw.strip().upper(), normalised, normalised.upper()):
        if candidate in mapping:
            return mapping[candidate]
    want_root, want_suffix = canonical(normalised)
    for k, v in mapping.items():
        root, suffix = canonical(k)
        if root != want_root:
            continue
        # An unsuffixed key ('0700') matches any market; a suffixed one must match
        # the exchange, so a US listing never inherits a Hong Kong mapping.
        if not suffix or not want_suffix or suffix == want_suffix:
            return v
    return None


def classify(
    ticker: str,
    currency: str,
    *,
    sectors: Mapping[str, str] | None = None,
    countries: Mapping[str, str] | None = None,
) -> Classification:
    sectors, countries = sectors or {}, countries or {}
    t = normalise_ticker(ticker, currency)
    sector = _lookup(sectors, t, ticker) or "Unclassified"
    country = _lookup(countries, t, ticker) or infer_country(t, currency)
    bucket = (
        SECTOR_BUCKET.get(sector, UNCLASSIFIED_BUCKET)
        if sector != "Unclassified"
        else "Unclassified"
    )
    return Classification(
        ticker=t,
        sector=sector,
        country=country,
        currency=currency_for(t, currency),
        bucket=bucket,
    )


def classify_all(
    tickers: list[tuple[str, str]],
    *,
    sectors: Mapping[str, str] | None = None,
    countries: Mapping[str, str] | None = None,
) -> dict[str, Classification]:
    return {
        normalise_ticker(t, c): classify(t, c, sectors=sectors, countries=countries)
        for t, c in tickers
    }
