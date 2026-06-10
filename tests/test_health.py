"""
tests/test_health.py
====================
Pruebas del endpoint de salud del backend Nexum.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_200() -> None:
    """El endpoint /health debe responder con HTTP 200."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_returns_correct_body() -> None:
    """El endpoint /health debe retornar el cuerpo esperado."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "nexum-backend"


@pytest.mark.asyncio
async def test_health_response_structure() -> None:
    """El endpoint /health debe retornar exactamente los campos esperados."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    data = response.json()
    assert set(data.keys()) == {"status", "service"}
