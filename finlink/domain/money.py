"""Money primitives. Decimal-only; floats are forbidden in financial code."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, getcontext

# FX inversion (1/7.8) and PnL division need headroom well beyond 28 significant
# digits, otherwise a pegged-currency round-trip drifts by 1e-28 and equality checks fail.
getcontext().prec = 50

QTY = Decimal("0.000001")
PRICE = Decimal("0.0001")
MONEY = Decimal("0.01")
PCT = Decimal("0.0001")

SUPPORTED_CURRENCIES = frozenset({"USD", "HKD", "SEK"})


class FXError(RuntimeError):
    """Raised when a required FX rate is missing or invalid."""


def d(value: str | int | float | Decimal) -> Decimal:
    """Construct a Decimal without ever passing through float binary error."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # str() of a float is the shortest repr that round-trips; acceptable but discouraged.
        return Decimal(str(value))
    return Decimal(str(value))


def q(value: Decimal, exp: Decimal = MONEY) -> Decimal:
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def to_usd(amount: Decimal, currency: str, rates: dict[str, Decimal]) -> Decimal:
    """Convert an amount from `currency` to USD.

    rates maps e.g. {"SEK": Decimal("0.095")} meaning 1 SEK = 0.095 USD.
    USD is identity. HKD is pegged at 7.8 by default (see config).
    """
    currency = currency.upper()
    if currency == "USD":
        return amount
    if currency not in SUPPORTED_CURRENCIES:
        raise FXError(f"unsupported currency: {currency!r}")
    rate = rates.get(currency)
    if rate is None:
        raise FXError(
            f"missing FX rate for {currency}; set it in config/config.yaml or run "
            f"`finlink fx-set {currency} <rate>`"
        )
    if rate <= 0:
        raise FXError(f"invalid FX rate for {currency}: {rate}")
    return amount * rate
