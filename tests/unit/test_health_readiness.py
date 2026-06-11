from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_session
from app.main import app


@pytest.mark.asyncio
async def test_readiness_returns_200():
    mock_session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_session
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/readiness")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_readiness_returns_503_on_db_failure():
    mock_session = AsyncMock()
    mock_session.execute.side_effect = Exception("DB error")
    app.dependency_overrides[get_db_session] = lambda: mock_session
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/readiness")
        assert response.status_code == 503
        assert response.json()["error_code"] == "infrastructure_error"

    app.dependency_overrides.clear()
