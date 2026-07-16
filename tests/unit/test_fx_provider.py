from decimal import Decimal
from unittest.mock import patch

import pytest

from app.fx.provider import DolarApiColombiaFxRateProvider, StaticFxRateProvider


@pytest.mark.asyncio
async def test_static_provider_returns_decimal():
    provider = StaticFxRateProvider({"USD_COP": Decimal("4100.00")})
    rate = await provider.get_rate("usd", "cop")
    assert isinstance(rate, Decimal)
    assert rate == Decimal("4100.00")


@pytest.mark.asyncio
async def test_static_provider_same_currency():
    provider = StaticFxRateProvider()
    rate = await provider.get_rate("USD", "USD")
    assert isinstance(rate, Decimal)
    assert rate == Decimal("1.00000000")


@pytest.mark.asyncio
async def test_dolarapi_provider_returns_decimal():
    provider = DolarApiColombiaFxRateProvider()
    from unittest.mock import MagicMock

    with patch("app.fx.provider.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.json.return_value = {"venta": 4150.50}
        mock_response.raise_for_status.return_value = None

        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        rate = await provider.get_rate("usd", "cop")
        assert isinstance(rate, Decimal)
        assert rate == Decimal("4150.5")


@pytest.mark.asyncio
async def test_dolarapi_provider_handles_invalid_value(caplog):
    provider = DolarApiColombiaFxRateProvider()
    from unittest.mock import MagicMock

    with patch("app.fx.provider.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.json.return_value = {"venta": "invalid_value"}
        mock_response.raise_for_status.return_value = None

        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        rate = await provider.get_rate("usd", "cop")
        assert rate is None
        assert "Error parseando tasa FX de DolarAPI a Decimal" in caplog.text


@pytest.mark.asyncio
async def test_dolarapi_provider_handles_http_error(caplog):
    provider = DolarApiColombiaFxRateProvider()

    with patch("app.fx.provider.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("HTTP Error")

        rate = await provider.get_rate("usd", "cop")
        assert rate is None
        assert "Error fetching FX rate from DolarAPI" in caplog.text
