"""FX rates from Frankfurter (ECB-backed, no API key).

Yahoo is unreliable for FX here, and a wrong SEK rate silently corrupts every
Swedish holding's PnL — worth having an independent source.
"""

from __future__ import annotations

from decimal import Decimal

from finlink.ingest.base import IngestError

ENDPOINT = "https://api.frankfurter.dev/v1/latest"


def fetch_rate_to_usd(currency: str, timeout_s: float = 15.0) -> tuple[Decimal, str]:
    """Return (rate, as_of) where 1 unit of `currency` = `rate` USD."""
    import json
    import urllib.request

    ccy = currency.upper()
    if ccy == "USD":
        return Decimal("1"), ""
    url = f"{ENDPOINT}?base={ccy}&symbols=USD"
    req = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "finlink/0.1 (+personal investment journal)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise IngestError(f"FX fetch failed for {ccy}: {e}") from e

    rate = (payload.get("rates") or {}).get("USD")
    if rate is None:
        raise IngestError(f"FX response had no USD rate for {ccy}: {payload}")
    return Decimal(str(rate)), str(payload.get("date") or "")
