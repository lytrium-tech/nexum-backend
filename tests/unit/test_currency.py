from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.currency import (
    FXProviderError,
    UnsupportedCurrencyError,
    get_fx_rate,
    get_minimum_unit,
    round_to_minimum_unit,
)


def test_get_minimum_unit():
    assert get_minimum_unit("COP") == Decimal("50.00")
    assert get_minimum_unit("USD") == Decimal("0.01")
    assert get_minimum_unit("EUR") == Decimal("0.01")
    assert get_minimum_unit("UNKNOWN") == Decimal("0.01")
    assert get_minimum_unit("cop") == Decimal("50.00")


def test_round_to_minimum_unit_cop():
    # Nearest 50 COP
    assert round_to_minimum_unit(Decimal("100"), "COP") == Decimal("100.00")
    assert round_to_minimum_unit(Decimal("124"), "COP") == Decimal("100.00")
    assert round_to_minimum_unit(Decimal("125"), "COP") == Decimal("150.00")
    assert round_to_minimum_unit(Decimal("126"), "COP") == Decimal("150.00")


def test_round_to_minimum_unit_usd():
    # Nearest 0.01 USD
    assert round_to_minimum_unit(Decimal("100.004"), "USD") == Decimal("100.00")
    assert round_to_minimum_unit(Decimal("100.005"), "USD") == Decimal("100.01")
    assert round_to_minimum_unit(Decimal("100.006"), "USD") == Decimal("100.01")


@pytest.mark.asyncio
async def test_get_fx_rate_same_currency():
    res = await get_fx_rate("COP", "COP")
    assert res["fx_rate"] == Decimal("1.0")
    assert res["rate_source"] == "internal"


@pytest.mark.asyncio
async def test_get_fx_rate_unsupported_currency():
    with pytest.raises(UnsupportedCurrencyError):
        await get_fx_rate("COP", "EUR")


@pytest.mark.asyncio
async def test_get_fx_rate_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "moneda": "USD",
        "nombre": "Dólar",
        "compra": 3450.00,
        "venta": 3460.00,
        "fechaActualizacion": "2026-06-27T12:00:00.000Z",
    }

    with patch(
        "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response
    ) as mock_get:
        res = await get_fx_rate("USD", "COP")
        assert res["fx_rate"] == Decimal("3460.00")
        assert res["rate_source"] == "dolarapi_colombia"
        mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_get_fx_rate_failure():
    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection error")):
        with pytest.raises(FXProviderError):
            await get_fx_rate("USD", "COP")
