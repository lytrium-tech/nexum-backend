import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture
def token_headers() -> dict[str, str]:
    return {}


@pytest.fixture(autouse=True)
def override_feature_flag(monkeypatch):
    monkeypatch.setattr(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", True)


@pytest.fixture(autouse=True)
def mock_db():
    from unittest.mock import AsyncMock, MagicMock

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    from app.core.database import get_db_session

    app.dependency_overrides[get_db_session] = lambda: mock_session
    yield mock_session
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_latest_rate_success(client: AsyncClient, token_headers: dict[str, str]):
    response = await client.get(
        "/api/v1/fx/rates/latest?base_currency=USD&quote_currency=COP", headers=token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["base_currency"] == "USD"
    assert "COP" in data["rates"]
    assert float(data["rates"]["COP"]) == 4000.0  # From StaticFxRateProvider


@pytest.mark.asyncio
async def test_get_latest_rate_feature_flag_off(
    client: AsyncClient, token_headers: dict[str, str], monkeypatch
):
    monkeypatch.setattr(settings, "NEXUM_OBLIGATIONS_V17_ENABLED", False)
    response = await client.get(
        "/api/v1/fx/rates/latest?base_currency=USD&quote_currency=COP", headers=token_headers
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "feature_flag_disabled"


@pytest.mark.asyncio
async def test_create_quote_success(client: AsyncClient, token_headers: dict[str, str]):
    payload = {"amount": "2.50", "from_currency": "USD", "to_currency": "COP"}
    response = await client.post("/api/v1/fx/quote", json=payload, headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["from_currency"] == "USD"
    assert data["to_currency"] == "COP"
    assert data["source_amount"] == "2.50"
    # rate is 4000.00 from StaticFxRateProvider
    assert data["target_amount"] == "10000.00"
    assert "expires_at" in data
    assert data["tolerance_bps"] == 50


@pytest.mark.asyncio
async def test_create_quote_same_currency(client: AsyncClient, token_headers: dict[str, str]):
    payload = {"amount": "100.00", "from_currency": "COP", "to_currency": "COP"}
    response = await client.post("/api/v1/fx/quote", json=payload, headers=token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["rate"] == "1.00000000"
    assert data["target_amount"] == "100.00"


@pytest.mark.asyncio
async def test_create_quote_invalid_amount(client: AsyncClient, token_headers: dict[str, str]):
    payload = {"amount": "0.00", "from_currency": "USD", "to_currency": "COP"}
    response = await client.post("/api/v1/fx/quote", json=payload, headers=token_headers)
    assert response.status_code == 422
