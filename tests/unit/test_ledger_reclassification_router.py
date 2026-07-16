from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.ledger.router import get_ledger_service
from app.ledger.schemas import ReclassificationResponse
from app.main import app


@pytest.fixture
def mock_service():
    service = AsyncMock()
    return service


@pytest.fixture
def test_client(mock_service):
    from app.users.dependencies import get_current_user_profile_dep
    from app.users.schemas import UserRead

    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    # Mock auth user
    mock_user = UserRead(
        id=uuid4(),
        email="test@example.com",
        name="Test",
        timezone="America/Bogota",
        currency="COP",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_current_user_profile_dep] = lambda: mock_user

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer mocked"}


def test_reclassify_endpoint_success(test_client, mock_service, auth_headers):
    event_id = uuid4()
    new_cat_id = uuid4()
    idem_key = uuid4()

    reclass_resp = ReclassificationResponse(
        status="reclassified",
        reclassification_id=uuid4(),
        event_id=event_id,
        previous_category_id=uuid4(),
        new_category_id=new_cat_id,
        idempotency_key=idem_key,
        source="manual",
        reason="Test",
        created_at=datetime.now(UTC),
    )
    mock_service.reclassify_event.return_value = reclass_resp

    payload = {
        "new_category_id": str(new_cat_id),
        "reason": "Test",
        "idempotency_key": str(idem_key),
    }

    resp = test_client.post(
        f"/api/v1/ledger/events/{event_id}/reclassify", json=payload, headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reclassified"
    assert data["new_category_id"] == str(new_cat_id)
    assert data["reason"] == "Test"


def test_reclassify_endpoint_reject_extra_fields(test_client, auth_headers):
    event_id = uuid4()
    payload = {
        "new_category_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "extra_field": "not allowed",
    }
    resp = test_client.post(
        f"/api/v1/ledger/events/{event_id}/reclassify", json=payload, headers=auth_headers
    )
    assert resp.status_code == 422


def test_reclassify_endpoint_reason_500(test_client, mock_service, auth_headers):
    event_id = uuid4()
    new_cat_id = uuid4()
    idem_key = uuid4()

    mock_service.reclassify_event.return_value = ReclassificationResponse(
        status="reclassified",
        reclassification_id=uuid4(),
        event_id=event_id,
        previous_category_id=uuid4(),
        new_category_id=new_cat_id,
        idempotency_key=idem_key,
        source="manual",
        reason="A" * 500,
        created_at=datetime.now(UTC),
    )

    payload = {
        "new_category_id": str(new_cat_id),
        "reason": "A" * 500,
        "idempotency_key": str(idem_key),
    }

    resp = test_client.post(
        f"/api/v1/ledger/events/{event_id}/reclassify", json=payload, headers=auth_headers
    )
    assert resp.status_code == 200


def test_reclassify_endpoint_reason_501(test_client, auth_headers):
    event_id = uuid4()
    payload = {
        "new_category_id": str(uuid4()),
        "reason": "A" * 501,
        "idempotency_key": str(uuid4()),
    }
    resp = test_client.post(
        f"/api/v1/ledger/events/{event_id}/reclassify", json=payload, headers=auth_headers
    )
    assert resp.status_code == 422
