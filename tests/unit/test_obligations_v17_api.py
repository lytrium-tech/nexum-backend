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

        response = await client.patch(
            "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods/00000000-0000-0000-0000-000000000000/amount",
            json={"amount": 100},
        )
        assert response.status_code == 403

        response = await client.post(
            "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods/00000000-0000-0000-0000-000000000000/payments",
            json={"amount": 100},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_v17_endpoints_empty_states(mock_db):
    """Test that V1.7 endpoints return expected empty state when flag is ON."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True

    from unittest.mock import MagicMock

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalars.return_value.first.return_value = None
    mock_db.execute.return_value = mock_result

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1.7/obligations")
            assert response.status_code == 200
            assert response.json() == []

            response = await client.get(
                "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000"
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "Obligation not found"

            response = await client.get(
                "/api/v1.7/obligations/00000000-0000-0000-0000-000000000000/periods"
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "Obligation not found"
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_v17_read_endpoints_success(mock_db):
    """Test GET endpoints for obligations and periods."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True

    import uuid
    from datetime import date, datetime
    from unittest.mock import MagicMock

    from app.obligations.models import Obligation, ObligationPeriod

    obs_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Mock Obligation",
        currency="USD",
        base_amount=Decimal("100.00"),
        status="active",
        type="indefinite",
        frequency="monthly",
        amount_type="fixed",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    mock_period = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        period_key="2026-07",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 7, 31),
        amount=Decimal("100.00"),
        currency="USD",
        paid_amount=0,
        status="pending_payment",
        is_current=True,
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "obligation_period" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [mock_period]
        else:
            mock_result.scalars.return_value.all.return_value = [mock_obligation]
            mock_result.scalars.return_value.first.return_value = mock_obligation
        return mock_result

    mock_db.execute.side_effect = side_effect

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1.7/obligations")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["id"] == str(obs_id)
            assert data[0]["name"] == "Mock Obligation"

            response = await client.get(f"/api/v1.7/obligations/{obs_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == str(obs_id)
            assert data["name"] == "Mock Obligation"

            response = await client.get(f"/api/v1.7/obligations/{obs_id}/periods")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["id"] == str(mock_period.id)
            assert data[0]["amount_due"] == "100.00"
            assert data[0]["amount_paid"] == "0"
            assert data[0]["status"] == "pending_payment"
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


@pytest.mark.asyncio
async def test_define_period_amount(mock_db):
    """Test define period amount validations and success."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True

    import uuid
    from datetime import date, datetime
    from unittest.mock import MagicMock

    from app.obligations.models import Obligation, ObligationPeriod

    obs_id = uuid.uuid4()
    period_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Mock Obligation",
        currency="COP",
        base_amount=None,
        status="active",
        type="indefinite",
        frequency="monthly",
        amount_type="variable",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    mock_period = ObligationPeriod(
        id=period_id,
        obligation_id=obs_id,
        period_key="2026-07",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 7, 31),
        amount=None,
        currency="COP",
        paid_amount=0,
        status="pending_amount_definition",
        is_current=True,
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "obligation_period" in stmt_str:
            mock_result.scalars.return_value.first.return_value = mock_period
        else:
            mock_result.scalars.return_value.first.return_value = mock_obligation
        return mock_result

    mock_db.execute.side_effect = side_effect

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Success
            response = await client.patch(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/amount",
                json={"amount": 1500.50, "currency": "COP"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["amount_due"] == "1500.5"
            assert data["status"] == "pending_payment"
            assert mock_period.amount == Decimal("1500.50")
            assert mock_period.status == "pending_payment"
            assert mock_db.commit.call_count >= 1

            # 2. <= 0 fails
            response = await client.patch(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/amount", json={"amount": 0}
            )
            assert response.status_code == 422

            # 3. Already defined
            mock_period.status = "pending_payment"
            response = await client.patch(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/amount", json={"amount": 100}
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "period_amount_already_defined"

            # Reset
            mock_period.status = "pending_amount_definition"

            # 4. Fixed obligation fails
            mock_obligation.amount_type = "fixed"
            response = await client.patch(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/amount", json={"amount": 100}
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "period_not_variable"
            mock_obligation.amount_type = "variable"

            # 5. Currency mismatch
            response = await client.patch(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/amount",
                json={"amount": 100, "currency": "USD"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "currency_mismatch"

            # 6. Obligation not found
            def side_effect_not_found(stmt):
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                return mock_result

            mock_db.execute.side_effect = side_effect_not_found
            response = await client.patch(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/amount", json={"amount": 100}
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "obligation_not_found"

    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_pay_specific_period(mock_db):
    """Test pay specific period validations and success."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True

    import uuid
    from datetime import date, datetime
    from unittest.mock import MagicMock

    from app.obligations.models import Obligation, ObligationPeriod

    obs_id = uuid.uuid4()
    period_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Mock Obligation",
        currency="COP",
        base_amount=Decimal("1500.00"),
        status="active",
        type="indefinite",
        frequency="monthly",
        amount_type="fixed",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    mock_period = ObligationPeriod(
        id=period_id,
        obligation_id=obs_id,
        period_key="2026-07",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 7, 31),
        amount=Decimal("1500.00"),
        currency="COP",
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        is_current=True,
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "obligationpayment" in stmt_str:
            mock_result.scalars.return_value.first.return_value = None
        elif "obligation_period" in stmt_str:
            mock_result.scalars.return_value.first.return_value = mock_period
        else:
            mock_result.scalars.return_value.first.return_value = mock_obligation
        return mock_result

    mock_db.execute.side_effect = side_effect

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Partial payment success
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 500.00, "currency": "COP"},
            )
            assert response.status_code == 201
            data = response.json()
            assert Decimal(data["payment"]["amount"]) == Decimal("500.00")
            assert data["period"]["status"] == "partially_paid"
            assert Decimal(data["period"]["amount_paid"]) == Decimal("500.00")

            assert mock_period.paid_amount == Decimal("500.00")
            assert mock_period.status == "partially_paid"

            # 2. Exact payment (remaining 1000)
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 1000.00, "currency": "COP"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["period"]["status"] == "paid"
            assert Decimal(data["period"]["amount_paid"]) == Decimal("1500.00")

            assert mock_period.paid_amount == Decimal("1500.00")
            assert mock_period.status == "paid"

            # 3. Already paid fails
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 100.00},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "period_not_payable"

            # Reset period to pending_payment for more tests
            mock_period.paid_amount = Decimal("0.00")
            mock_period.status = "pending_payment"

            # 4. Overpayment fails
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 2000.00},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "payment_exceeds_remaining_amount"

            # 5. <= 0 fails
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments", json={"amount": 0}
            )
            assert response.status_code == 422

            # 6. Currency mismatch
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 100.00, "currency": "USD"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "currency_mismatch"

            # 7. Period amount not defined
            mock_period.amount = None
            mock_period.status = "pending_amount_definition"
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 100.00},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "period_amount_not_defined"

            # 8. Obligation not found
            def side_effect_not_found(stmt):
                mock_result = MagicMock()
                mock_result.scalars.return_value.first.return_value = None
                return mock_result

            mock_db.execute.side_effect = side_effect_not_found
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments", json={"amount": 100}
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "obligation_not_found"

            # 9. Idempotency Conflict
            def side_effect_idempotency(stmt):
                mock_result = MagicMock()
                stmt_str = str(stmt).lower()
                if "obligationpayment" in stmt_str:
                    mock_result.scalars.return_value.first.return_value = "EXISTING_PAYMENT"
                elif "obligation_period" in stmt_str:
                    mock_result.scalars.return_value.first.return_value = mock_period
                else:
                    mock_result.scalars.return_value.first.return_value = mock_obligation
                return mock_result

            mock_db.execute.side_effect = side_effect_idempotency
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/payments",
                json={"amount": 100, "idempotency_key": "req-123"},
            )
            assert response.status_code == 409
            assert response.json()["detail"] == "idempotency_conflict"

    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_pay_obligation_fifo(mock_db):
    """Test pay obligation FIFO strategy."""
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True

    import uuid
    from datetime import date, datetime
    from unittest.mock import MagicMock
    from decimal import Decimal

    from app.obligations.models import Obligation, ObligationPeriod

    obs_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_obligation = Obligation(
        id=obs_id,
        user_id=str(user_id),
        name="Mock Obligation",
        currency="COP",
        base_amount=Decimal("1500.00"),
        status="active",
        type="indefinite",
        frequency="monthly",
        amount_type="fixed",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    mock_period_1 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        period_key="2026-07",
        sequence_number=1,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
        due_date=date(2026, 7, 31),
        amount=Decimal("1000.00"),
        currency="COP",
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        is_current=True,
    )

    mock_period_2 = ObligationPeriod(
        id=uuid.uuid4(),
        obligation_id=obs_id,
        period_key="2026-08",
        sequence_number=2,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        due_date=date(2026, 8, 31),
        amount=Decimal("1500.00"),
        currency="COP",
        paid_amount=Decimal("0.00"),
        status="pending_payment",
        is_current=False,
    )

    def side_effect(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "obligationpayment" in stmt_str:
            mock_result.scalars.return_value.first.return_value = None
        elif "obligation_period" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [mock_period_1, mock_period_2]
        else:
            mock_result.scalars.return_value.first.return_value = mock_obligation
        return mock_result

    mock_db.execute.side_effect = side_effect

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Partial payment to first period
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/payments",
                json={"amount": 500.00, "currency": "COP"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["strategy"] == "fifo"
            assert len(data["payments"]) == 1
            assert Decimal(data["payments"][0]["amount"]) == Decimal("500.00")
            assert mock_period_1.paid_amount == Decimal("500.00")
            assert mock_period_1.status == "partially_paid"
            assert mock_period_2.paid_amount == Decimal("0.00")

            # 2. Payment crossing multiple periods (remaining 500 of p1 + 1000 of p2)
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/payments",
                json={"amount": 1500.00, "currency": "COP"},
            )
            assert response.status_code == 201
            data = response.json()
            assert len(data["payments"]) == 2
            assert Decimal(data["payments"][0]["amount"]) == Decimal("500.00")
            assert Decimal(data["payments"][1]["amount"]) == Decimal("1000.00")
            assert mock_period_1.paid_amount == Decimal("1000.00")
            assert mock_period_1.status == "paid"
            assert mock_period_2.paid_amount == Decimal("1000.00")
            assert mock_period_2.status == "partially_paid"

            # 3. Overpayment fails (p2 has 500 remaining)
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/payments",
                json={"amount": 1000.00, "currency": "COP"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "payment_exceeds_total_remaining_amount"

            # 4. No payable periods
            mock_period_1.status = "paid"
            mock_period_2.status = "paid"

            def side_effect_empty(stmt):
                mock_result = MagicMock()
                stmt_str = str(stmt).lower()
                if "obligationpayment" in stmt_str:
                    mock_result.scalars.return_value.first.return_value = None
                elif "obligation_period" in stmt_str:
                    mock_result.scalars.return_value.all.return_value = []
                else:
                    mock_result.scalars.return_value.first.return_value = mock_obligation
                return mock_result

            mock_db.execute.side_effect = side_effect_empty
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/payments",
                json={"amount": 100.00, "currency": "COP"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "no_payable_periods"

            # 5. Idempotency Conflict
            def side_effect_idempotency(stmt):
                mock_result = MagicMock()
                stmt_str = str(stmt).lower()
                if "obligationpayment" in stmt_str:
                    mock_result.scalars.return_value.first.return_value = "EXISTING_PAYMENT"
                return mock_result

            mock_db.execute.side_effect = side_effect_idempotency
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/payments",
                json={"amount": 100, "idempotency_key": "req-123"},
            )
            assert response.status_code == 409
            assert response.json()["detail"] == "idempotency_conflict"

    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_skip_period_success(mock_db):
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    try:
        import uuid
        from decimal import Decimal
        from unittest.mock import MagicMock
        from app.obligations.models import ObligationPeriod, Obligation

        obs_id = uuid.uuid4()
        period_id = uuid.uuid4()

        from datetime import date

        mock_obl = Obligation(id=obs_id, currency="COP", amount_type="fixed")
        mock_period = ObligationPeriod(
            id=period_id,
            obligation_id=obs_id,
            status="pending_payment",
            amount=Decimal("100"),
            paid_amount=Decimal("0"),
            is_current=True,
            due_date=date.today(),
        )

        def side_effect(stmt):
            mock_result = MagicMock()
            if "obligation_period" in str(stmt).lower():
                mock_result.scalars.return_value.first.return_value = mock_period
            else:
                mock_result.scalars.return_value.first.return_value = mock_obl
            return mock_result

        mock_db.execute.side_effect = side_effect

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/skip")
            assert response.status_code == 200
            assert mock_period.status == "skipped"
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_cancel_period_success(mock_db):
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    try:
        import uuid
        from decimal import Decimal
        from unittest.mock import MagicMock
        from app.obligations.models import ObligationPeriod, Obligation

        obs_id = uuid.uuid4()
        period_id = uuid.uuid4()

        from datetime import date

        mock_obl = Obligation(id=obs_id, currency="COP", amount_type="fixed")
        mock_period = ObligationPeriod(
            id=period_id,
            obligation_id=obs_id,
            status="pending_payment",
            amount=Decimal("100"),
            paid_amount=Decimal("0"),
            is_current=True,
            due_date=date.today(),
        )

        def side_effect(stmt):
            mock_result = MagicMock()
            if "obligation_period" in str(stmt).lower():
                mock_result.scalars.return_value.first.return_value = mock_period
            else:
                mock_result.scalars.return_value.first.return_value = mock_obl
            return mock_result

        mock_db.execute.side_effect = side_effect

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1.7/obligations/{obs_id}/periods/{period_id}/cancel"
            )
            assert response.status_code == 200
            assert mock_period.status == "cancelled"
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False


@pytest.mark.asyncio
async def test_refresh_overdue_periods(mock_db):
    settings.NEXUM_OBLIGATIONS_V17_ENABLED = True
    try:
        import uuid
        from decimal import Decimal
        from datetime import date, timedelta
        from unittest.mock import MagicMock
        from app.obligations.models import ObligationPeriod, Obligation

        obs_id = uuid.uuid4()
        period_id = uuid.uuid4()

        mock_obl = Obligation(id=obs_id, currency="COP", amount_type="fixed")
        mock_period = ObligationPeriod(
            id=period_id,
            obligation_id=obs_id,
            status="pending_payment",
            due_date=date.today() - timedelta(days=1),
            amount=Decimal("100"),
            paid_amount=Decimal("0"),
            is_current=True,
        )

        def side_effect(stmt):
            mock_result = MagicMock()
            if "obligation_period" in str(stmt).lower():
                mock_result.scalars.return_value.all.return_value = [mock_period]
            else:
                mock_result.scalars.return_value.first.return_value = mock_obl
            return mock_result

        mock_db.execute.side_effect = side_effect

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1.7/obligations/{obs_id}/periods/refresh-overdue")
            assert response.status_code == 200
            assert mock_period.status == "overdue"
            assert response.json()["updated_count"] == 1
    finally:
        settings.NEXUM_OBLIGATIONS_V17_ENABLED = False
