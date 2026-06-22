import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class FxRateProvider(Protocol):
    async def get_rate(self, from_currency: str, to_currency: str) -> float | None:
        """
        Devuelve la tasa de cambio de from_currency a to_currency.
        Ej: get_rate("USD", "COP") -> 4000.0
        Si no se soporta o hay un error, devuelve None.
        """
        ...


class StaticFxRateProvider(FxRateProvider):
    """Proveedor estático para tests y fallback local."""

    def __init__(self, rates: dict[str, float] | None = None):
        self.rates = rates or {
            "USD_COP": 4000.0,
            "EUR_COP": 4300.0,
        }

    async def get_rate(self, from_currency: str, to_currency: str) -> float | None:
        if from_currency == to_currency:
            return 1.0
        pair = f"{from_currency.upper()}_{to_currency.upper()}"
        return self.rates.get(pair)


class DolarApiColombiaFxRateProvider(FxRateProvider):
    """
    Proveedor real usando https://co.dolarapi.com
    Solo soporta USD -> COP por ahora.
    """

    def __init__(self, base_url: str = "https://co.dolarapi.com", timeout_seconds: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def get_rate(self, from_currency: str, to_currency: str) -> float | None:
        from_c = from_currency.upper()
        to_c = to_currency.upper()

        if from_c == to_c:
            return 1.0

        if from_c == "USD" and to_c == "COP":
            url = f"{self.base_url}/v1/cotizaciones/usd"
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()
                    # Retornamos el valor de 'venta' por defecto para el calculo del snapshot
                    return float(data.get("venta", 0.0))
            except Exception as e:
                logger.warning(f"Error fetching FX rate from DolarAPI: {e}")
                return None

        # Otras monedas no soportadas en V1.4
        return None
