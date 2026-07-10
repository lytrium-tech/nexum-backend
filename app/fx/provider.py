import logging
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class FxRateProvider(Protocol):
    async def get_rate(self, from_currency: str, to_currency: str) -> Decimal | None:
        """
        Devuelve la tasa de cambio de from_currency a to_currency.
        Ej: get_rate("USD", "COP") -> Decimal('4000.00000000')
        Si no se soporta o hay un error, devuelve None.
        """
        ...


class StaticFxRateProvider(FxRateProvider):
    """Proveedor estático para tests y fallback local."""

    def __init__(self, rates: dict[str, Decimal] | None = None):
        self.rates = rates or {
            "USD_COP": Decimal("4000.00"),
            "EUR_COP": Decimal("4300.00"),
        }

    async def get_rate(self, from_currency: str, to_currency: str) -> Decimal | None:
        from_c = from_currency.upper()
        to_c = to_currency.upper()
        if from_c == to_c:
            return Decimal("1.00000000")
        pair = f"{from_c}_{to_c}"
        if pair in self.rates:
            return self.rates[pair]
        
        inverse_pair = f"{to_c}_{from_c}"
        if inverse_pair in self.rates:
            inverse_rate = self.rates[inverse_pair]
            if inverse_rate and inverse_rate > Decimal("0"):
                return (Decimal("1") / inverse_rate).quantize(Decimal("0.00000000"))
            
        return None


class DolarApiColombiaFxRateProvider(FxRateProvider):
    """
    Proveedor real usando https://co.dolarapi.com
    Solo soporta USD -> COP por ahora.
    """

    def __init__(self, base_url: str = "https://co.dolarapi.com", timeout_seconds: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def get_rate(self, from_currency: str, to_currency: str) -> Decimal | None:
        from_c = from_currency.upper()
        to_c = to_currency.upper()

        if from_c == to_c:
            return Decimal("1.00000000")

        if from_c == "USD" and to_c == "COP":
            return await self._fetch_usd_cop_rate()

        if from_c == "COP" and to_c == "USD":
            usd_cop_rate = await self._fetch_usd_cop_rate()
            if usd_cop_rate and usd_cop_rate > Decimal("0"):
                return (Decimal("1") / usd_cop_rate).quantize(Decimal("0.00000000"))
            return None

        # Otras monedas no soportadas
        return None

    async def _fetch_usd_cop_rate(self) -> Decimal | None:
        url = f"{self.base_url}/v1/cotizaciones/usd"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                venta_val = str(data.get("venta", "0.0"))
                return Decimal(venta_val)
        except InvalidOperation as e:
            logger.warning(f"Error parseando tasa FX de DolarAPI a Decimal: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching FX rate from DolarAPI: {e}")
            return None
