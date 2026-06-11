import pytest
from httpx import ASGITransport, AsyncClient
from app.main import create_app
from app.core.config import settings
from unittest.mock import patch

@pytest.mark.asyncio
async def test_cors_preflight_allowed():
    with patch.object(settings, "CORS_ORIGINS", "http://localhost:3000"):
        with patch.object(settings, "DEBUG", False):
            with patch.object(settings, "APP_ENV", "production"):
                test_app = create_app()
                async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                    headers = {
                        "Origin": "http://localhost:3000",
                        "Access-Control-Request-Method": "GET",
                    }
                    response = await client.options("/health", headers=headers)
                    assert response.status_code == 200
                    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

@pytest.mark.asyncio
async def test_cors_preflight_blocked():
    with patch.object(settings, "CORS_ORIGINS", "http://localhost:3000"):
        with patch.object(settings, "DEBUG", False):
            test_app = create_app()
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                headers = {
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                }
                response = await client.options("/health", headers=headers)
                assert response.status_code == 400

@pytest.mark.asyncio
async def test_docs_hidden_in_production():
    with patch.object(settings, "DEBUG", False):
        with patch.object(settings, "APP_ENV", "production"):
            test_app = create_app()
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                response = await client.get("/docs")
                assert response.status_code == 404
                response = await client.get("/openapi.json")
                assert response.status_code == 404

@pytest.mark.asyncio
async def test_docs_visible_in_development():
    with patch.object(settings, "DEBUG", True):
        with patch.object(settings, "APP_ENV", "development"):
            test_app = create_app()
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                response = await client.get("/docs")
                assert response.status_code == 200
                response = await client.get("/openapi.json")
                assert response.status_code == 200
