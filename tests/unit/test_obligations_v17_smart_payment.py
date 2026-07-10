import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.users.dependencies import get_current_user_profile_dep


@pytest.fixture
def mock_auth():
    async def mock_get_current_user():
        from datetime import UTC, datetime

        from app.users.schemas import UserRead
        return UserRead(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            auth_user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            name="Test User",
            email="test@test.com",
            timezone="America/Bogota",
            currency="USD",
            status="active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    from unittest.mock import AsyncMock

    from app.core.database import get_db_session
    mock_session = AsyncMock()
    app.dependency_overrides[get_current_user_profile_dep] = mock_get_current_user
    app.dependency_overrides[get_db_session] = lambda: mock_session
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    yield
    app.dependency_overrides.clear()

@pytest.fixture
async def async_client(mock_auth):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_smart_payment_delegates_to_service(async_client, monkeypatch):
    from datetime import date
    from unittest.mock import AsyncMock

    from app.obligations.models import ObligationPayment, ObligationPeriod
    from app.obligations.service_v17 import ObligationV17Service
    
    mock_pay = AsyncMock(return_value=(
        [ObligationPayment(
            id=uuid.uuid4(),
            obligation_id=uuid.uuid4(),
            obligation_period_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            amount=Decimal("100"),
            currency="USD",
            source_amount=Decimal("100"),
            source_currency="USD",
            fx_rate=Decimal("1.0")
        )], 
        [ObligationPeriod(
            id=uuid.uuid4(), 
            obligation_id=uuid.uuid4(),
            status="paid", 
            amount=Decimal("100"), 
            paid_amount=Decimal("100"),
            due_date=date(2026, 1, 5),
            is_current=False
        )]
    ))
    monkeypatch.setattr(ObligationV17Service, "pay_obligation_smart", mock_pay)
    
    obligation_id = uuid.uuid4()
    payload = {"amount": 100}
    response = await async_client.post(f"/api/v1.7/obligations/{obligation_id}/payments/smart", json=payload)
    
    assert response.status_code == 200
    assert response.json()["strategy"] == "smart"
    assert len(response.json()["payments"]) == 1
    assert len(response.json()["periods"]) == 1

@pytest.mark.asyncio
async def test_smart_payment_rejects_pending_amount_definition(async_client, monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from app.obligations.service_v17 import ObligationV17Service
    
    # We simulate that pay_obligation_smart throws the correct exception
    mock_pay = AsyncMock(side_effect=HTTPException(status_code=422, detail="oldest_period_requires_amount_definition"))
    monkeypatch.setattr(ObligationV17Service, "pay_obligation_smart", mock_pay)
    
    obligation_id = uuid.uuid4()
    payload = {"amount": 100}
    response = await async_client.post(f"/api/v1.7/obligations/{obligation_id}/payments/smart", json=payload)
    
    assert response.status_code == 422
    assert response.json()["detail"] == "oldest_period_requires_amount_definition"
