import uuid
from decimal import Decimal
from datetime import date, datetime, UTC

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.users.dependencies import get_current_user_profile_dep

@pytest.fixture
def mock_auth():
    async def mock_get_current_user():
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
async def test_obligations_overview_returns_relevant_period(async_client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.obligations.service_v17 import ObligationV17Service
    from app.obligations.models import ObligationPeriod
    
    mock_overview = AsyncMock(return_value=[{
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Test",
        "currency": "COP",
        "frequency": "monthly",
        "amount": Decimal("40000"),
        "amount_type": "fixed",
        "obligation_type": "recurring",
        "status": "active",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "relevant_period": ObligationPeriod(
            id=uuid.uuid4(),
            obligation_id=uuid.uuid4(),
            status="pending_payment",
            due_date=date(2026, 7, 8),
            amount=Decimal("40000"),
            paid_amount=Decimal("0"),
            is_current=True
        ),
        "period_counts": {
            "payable": 1,
            "overdue": 0,
            "pending": 1,
            "paid": 0,
            "cancelled": 0,
            "skipped": 0
        },
        "action_state": {
            "can_pay": True,
            "requires_amount_definition": False,
            "can_skip": True,
            "can_cancel": True,
            "has_overdue": False,
            "payable_period_count": 1,
            "payable_total_amount": "40000"
        }
    }])
    monkeypatch.setattr(ObligationV17Service, "list_obligations_overview", mock_overview)
    
    response = await async_client.get("/api/v1.7/obligations/overview")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["relevant_period"]["status"] == "pending_payment"
    assert data[0]["action_state"]["can_pay"] is True
    assert data[0]["period_counts"]["payable"] == 1

@pytest.mark.asyncio
async def test_obligations_overview_variable_requires_amount_definition(async_client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.obligations.service_v17 import ObligationV17Service
    from app.obligations.models import ObligationPeriod
    
    mock_overview = AsyncMock(return_value=[{
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Test Var",
        "currency": "COP",
        "frequency": "monthly",
        "amount": Decimal("0"),
        "amount_type": "variable",
        "obligation_type": "recurring",
        "status": "active",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "relevant_period": ObligationPeriod(
            id=uuid.uuid4(),
            obligation_id=uuid.uuid4(),
            status="pending_amount_definition",
            due_date=date(2026, 7, 8),
            is_current=True
        ),
        "period_counts": {
            "payable": 1,
            "overdue": 0,
            "pending": 1,
            "paid": 0,
            "cancelled": 0,
            "skipped": 0
        },
        "action_state": {
            "can_pay": False,
            "requires_amount_definition": True,
            "can_skip": True,
            "can_cancel": True,
            "has_overdue": False,
            "payable_period_count": 1,
            "payable_total_amount": "0"
        }
    }])
    monkeypatch.setattr(ObligationV17Service, "list_obligations_overview", mock_overview)
    
    response = await async_client.get("/api/v1.7/obligations/overview")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["action_state"]["requires_amount_definition"] is True
    assert data[0]["action_state"]["can_pay"] is False

@pytest.mark.asyncio
async def test_overview_returns_200_with_obligation_without_periods(async_client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.obligations.service_v17 import ObligationV17Service
    
    mock_overview = AsyncMock(return_value=[{
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "No Periods",
        "currency": "COP",
        "frequency": "monthly",
        "amount": Decimal("0"),
        "amount_type": "variable",
        "obligation_type": "recurring",
        "status": "active",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "relevant_period": None,
        "period_counts": {
            "payable": 0, "overdue": 0, "pending": 0,
            "paid": 0, "cancelled": 0, "skipped": 0
        },
        "action_state": {
            "can_pay": False, "requires_amount_definition": False,
            "can_skip": False, "can_cancel": False, "has_overdue": False,
            "payable_period_count": 0, "payable_total_amount": "0"
        }
    }])
    monkeypatch.setattr(ObligationV17Service, "list_obligations_overview", mock_overview)
    
    response = await async_client.get("/api/v1.7/obligations/overview")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["relevant_period"] is None
    assert data[0]["period_counts"]["payable"] == 0
    assert data[0]["action_state"]["can_pay"] is False

@pytest.mark.asyncio
async def test_overview_handles_legacy_obligation_missing_optional_fields(async_client, monkeypatch):
    from unittest.mock import AsyncMock
    from app.obligations.service_v17 import ObligationV17Service
    
    mock_overview = AsyncMock(return_value=[{
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "name": "Legacy",
        "currency": "USD",
        "frequency": "monthly",
        "amount": Decimal("0"),
        "amount_type": None,
        "obligation_type": "recurring",
        "status": "active",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "relevant_period": None,
        "period_counts": {
            "payable": 0, "overdue": 0, "pending": 0,
            "paid": 0, "cancelled": 0, "skipped": 0
        },
        "action_state": {
            "can_pay": False, "requires_amount_definition": False,
            "can_skip": False, "can_cancel": False, "has_overdue": False,
            "payable_period_count": 0, "payable_total_amount": "0"
        }
    }])
    monkeypatch.setattr(ObligationV17Service, "list_obligations_overview", mock_overview)
    
    response = await async_client.get("/api/v1.7/obligations/overview")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["amount_type"] is None
