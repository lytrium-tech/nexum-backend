from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_db_session
from app.main import app


@pytest.fixture
def mock_db():
    mock_session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_session
    yield mock_session
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_v17_endpoints_disabled_by_default(mock_db):
    """Test that V1.7 endpoints return 403 when feature flag is OFF."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1.7/obligations")
        assert response.status_code == 403
        assert response.json()["detail"] == "feature_flag_disabled"

        response = await client.get("/api/v1.7/obligations/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 403

        response = await client.get("/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods")
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_v17_endpoints_enabled(mock_db):
    """Test that V1.7 endpoints return expected empty state when flag is ON."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1.7/obligations")
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "No obligations found"
            assert data["items"] == []

            response = await client.get("/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods")
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "No periods found"
            assert data["items"] == []

            response = await client.get("/api/v1.7/obligations/00000000-0000-0000-0000-000000000000")
            assert response.status_code == 404
            assert response.json()["detail"] == "Obligation not found"
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False
