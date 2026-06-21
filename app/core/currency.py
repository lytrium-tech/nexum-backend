from decimal import Decimal

CURRENCY_MINIMUM_UNITS = {
    "COP": Decimal("50.00"),
    "USD": Decimal("0.01"),
    "EUR": Decimal("0.01"),
}

def get_minimum_unit(currency: str) -> Decimal:
    """Returns the minimum valid unit for a given currency."""
    return CURRENCY_MINIMUM_UNITS.get(currency.upper(), Decimal("0.01"))

def round_to_minimum_unit(amount: Decimal, currency: str) -> Decimal:
    """Rounds an amount to the nearest valid minimum unit for the given currency."""
    min_unit = get_minimum_unit(currency)
    if min_unit == Decimal("0.00") or min_unit == Decimal("0"):
        return amount
    
    # We round to the nearest multiple of min_unit
    # amount / min_unit, round, then * min_unit
    return (amount / min_unit).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * min_unit
