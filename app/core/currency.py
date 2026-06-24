from datetime import UTC, datetime
from decimal import Decimal

import httpx

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
    return (amount / min_unit).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * min_unit


def round_up_to_minimum_unit(amount: Decimal, currency: str) -> Decimal:
    """Rounds an amount UP to the nearest valid minimum unit for the given currency."""
    min_unit = get_minimum_unit(currency)
    if min_unit == Decimal("0.00") or min_unit == Decimal("0"):
        return amount

    # We round up to the next multiple of min_unit
    return (amount / min_unit).quantize(Decimal("1"), rounding="ROUND_CEILING") * min_unit


class FXProviderError(Exception):
    pass


class UnsupportedCurrencyError(Exception):
    pass


async def get_fx_rate(source_currency: str, target_currency: str) -> dict:
    """
    Returns dict:
    {
        "fx_rate": Decimal,
        "rate_source": str,
        "rate_timestamp": datetime
    }
    """
    source_currency = (source_currency or "COP").upper()
    target_currency = (target_currency or "COP").upper()

    if source_currency == target_currency:
        return {
            "fx_rate": Decimal("1.0"),
            "rate_source": "internal",
            "rate_timestamp": datetime.now(UTC),
        }

    # Only USD/COP is supported
    if {source_currency, target_currency} != {"USD", "COP"}:
        raise UnsupportedCurrencyError(
            f"Conversión entre {source_currency} y {target_currency} no soportada."
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.dolarapi.com/v1/dolares/oficial")
            resp.raise_for_status()
            data = resp.json()
            # Dólar API returns venta/compra. We'll use venta for generic fx rate, or average.
            # Let's use 'venta' as a simple standard for Nexum.
            usd_to_cop = Decimal(str(data["venta"]))
            rate_ts = datetime.fromisoformat(data["fechaActualizacion"].replace("Z", "+00:00"))
    except Exception as e:
        raise FXProviderError(f"Error consultando Dólar API: {str(e)}")

    if source_currency == "USD" and target_currency == "COP":
        return {
            "fx_rate": usd_to_cop,
            "rate_source": "dolarapi_colombia",
            "rate_timestamp": rate_ts,
        }
    else:
        # COP to USD
        return {
            "fx_rate": Decimal("1.0") / usd_to_cop,
            "rate_source": "dolarapi_colombia_inverse",
            "rate_timestamp": rate_ts,
        }
