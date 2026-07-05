from decimal import Decimal
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

        response = await client.get(
            "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods"
        )
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
            assert data == []

            response = await client.get(
                "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "No periods found"
            assert data["items"] == []

            response = await client.get(
                "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000"
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "Obligation not found"
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_create_obligation_minimal(mock_db):
    """Test POST creates a minimal obligation."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1.7/obligations",
                json={
                    "name": "Test Minimal",
                    "obligation_type": "recurring",
                    "frequency": "monthly",
                    "amount_type": "fixed",
                    "base_amount": 500.50,
                    "start_date": "2026-07-01",
                    "first_due_date": "2026-07-05",
                },
            )
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "Test Minimal"
            assert data["currency"] == "COP"  # Default
            assert data["amount"] == "500.5"
            assert data["status"] == "active"

            # Verify DB was called with Obligation and ObligationPeriod
            assert mock_db.add.call_count == 2
            added_objects = [call.args[0] for call in mock_db.add.call_args_list]

            obligation = added_objects[0]
            assert obligation.__class__.__name__ == "Obligation"

            period = added_objects[1]
            assert period.__class__.__name__ == "ObligationPeriod"
            assert period.sequence_number == 1
            assert period.period_key == "2026-07"
            assert period.start_date.isoformat() == "2026-07-01"
            assert period.end_date.isoformat() == "2026-07-05"
            assert period.due_date.isoformat() == "2026-07-05"
            assert period.amount == Decimal("500.50")
            assert period.status == "pending_payment"
            assert period.is_current is True
            assert period.paid_amount == 0
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_create_obligation_validations(mock_db):
    """Test validations for creating an obligation."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # fixed sin base_amount falla
            response = await client.post(
                "/api/v1.7/obligations",
                json={
                    "name": "Test",
                    "obligation_type": "recurring",
                    "frequency": "monthly",
                    "amount_type": "fixed",
                    "start_date": "2026-07-01",
                    "first_due_date": "2026-07-05",
                },
            )
            assert response.status_code == 422

            # fixed con base_amount <= 0 falla
            response = await client.post(
                "/api/v1.7/obligations",
                json={
                    "name": "Test",
                    "obligation_type": "recurring",
                    "frequency": "monthly",
                    "amount_type": "fixed",
                    "base_amount": -10,
                    "start_date": "2026-07-01",
                    "first_due_date": "2026-07-05",
                },
            )
            assert response.status_code == 422

            # variable permite base_amount null
            response = await client.post(
                "/api/v1.7/obligations",
                json={
                    "name": "Test",
                    "obligation_type": "recurring",
                    "frequency": "monthly",
                    "amount_type": "variable",
                    "start_date": "2026-07-01",
                    "first_due_date": "2026-07-05",
                },
            )
            assert response.status_code == 201
            assert response.json()["amount"] == "0"

            # verify variable period properties
            added_objects = [call.args[0] for call in mock_db.add.call_args_list]
            period = added_objects[-1]
            assert period.__class__.__name__ == "ObligationPeriod"
            assert period.amount is None
            assert period.status == "pending_amount_definition"

            # first_due_date antes de start_date falla
            response = await client.post(
                "/api/v1.7/obligations",
                json={
                    "name": "Test",
                    "obligation_type": "recurring",
                    "frequency": "monthly",
                    "amount_type": "variable",
                    "start_date": "2026-07-05",
                    "first_due_date": "2026-07-01",
                },
            )
            assert response.status_code == 422
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False
