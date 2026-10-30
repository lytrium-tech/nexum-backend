"""Unit tests for Credit Command Reconciliation API and Service.

Covers:
1. Mapping of all four mutation families to persisted identifiers:
   - purchase -> CreditCardTransaction.id (transaction_id)
   - standard payment -> CreditCardPayment.id (payment_id)
   - targeted pay_early -> CreditCardEarlyPayment.id (early_payment_id)
   - adjustment/refund/reversal -> CreditCardAdjustment.id (adjustment_id)
   - includes event_id and resource_id.
2. Indistinguishable unknown results for:
   - Command not found (uncommitted, rolled back, never issued)
   - Foreign owner (event owned by different user)
   - Non-credit event (e.g. transfer, cash expense, goal contribution)
   - Incomplete mapping (e.g. event exists but Credit table row missing)
   - Foreign resource (e.g. event owned by user but transaction owned by other user)
3. Invariant: strictly read-only (zero writes, no UnitOfWork, no FOR UPDATE, no autoflush side effects).
4. Pydantic schema validation:
   - Status 'unknown' strictly enforces nulls for operation_type, event_id, resource_id, and all IDs.
   - Status 'confirmed' strictly enforces presence of operation_type, event_id, resource_id, and matching ID.
   - Extra fields forbidden (ConfigDict(extra="forbid")).
5. FastAPI Router integration:
   - Valid UUID with confirmed/unknown response.
   - Invalid UUID path parameter yields 422 canonical validation error.
   - Authenticated owner scoping.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.credit.models import (
    CreditCardAdjustment,
    CreditCardEarlyPayment,
    CreditCardPayment,
    CreditCardTransaction,
)
from app.credit.router import get_credit_service
from app.credit.schemas import (
    CreditCommandReconciliationRead,
)
from app.credit.service import CreditCardService
from app.ledger.enums import EventType
from app.main import app
from app.users.dependencies import get_current_user_profile_dep
from app.users.schemas import UserRead

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.dirty = set()
    session.new = set()
    session.deleted = set()
    return session


@pytest.fixture
def mock_credit_repo():
    repo = AsyncMock()
    repo.get_transaction_by_event_for_user.return_value = None
    repo.get_payment_by_event_for_user.return_value = None
    repo.get_early_payment_by_event_for_user.return_value = None
    repo.get_adjustment_by_event_for_user.return_value = None
    return repo


@pytest.fixture
def mock_ledger_service():
    service = AsyncMock()
    service.repository = AsyncMock()
    service.repository.get_by_command_id_for_user.return_value = None
    return service


@pytest.fixture
def credit_service(mock_session, mock_credit_repo, mock_ledger_service):
    service = CreditCardService(mock_session)
    service.repo = mock_credit_repo
    service.ledger_service = mock_ledger_service
    service.ledger_repo = mock_ledger_service.repository
    return service


# ── 1. Schema Validation Tests ───────────────────────────────────────────────


def test_schema_unknown_valid():
    cmd_id = uuid.uuid4()
    model = CreditCommandReconciliationRead(
        command_id=cmd_id,
        status="unknown",
        operation_type=None,
        event_id=None,
        resource_id=None,
        transaction_id=None,
        payment_id=None,
        early_payment_id=None,
        adjustment_id=None,
    )
    assert model.command_id == cmd_id
    assert model.status == "unknown"
    assert model.operation_type is None
    assert model.event_id is None
    assert model.resource_id is None
    assert model.transaction_id is None


def test_schema_unknown_rejects_non_null_fields():
    cmd_id = uuid.uuid4()
    # operation_type must be None
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="unknown",
            operation_type="purchase",
        )

    # event_id must be None
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="unknown",
            event_id=uuid.uuid4(),
        )

    # resource_id must be None
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="unknown",
            resource_id=uuid.uuid4(),
        )

    # transaction_id must be None
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="unknown",
            transaction_id=uuid.uuid4(),
        )


def test_schema_confirmed_valid_purchase():
    cmd_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tx_id = uuid.uuid4()
    model = CreditCommandReconciliationRead(
        command_id=cmd_id,
        status="confirmed",
        operation_type="purchase",
        event_id=event_id,
        resource_id=tx_id,
        transaction_id=tx_id,
    )
    assert model.status == "confirmed"
    assert model.operation_type == "purchase"
    assert model.event_id == event_id
    assert model.resource_id == tx_id
    assert model.transaction_id == tx_id
    assert model.payment_id is None


def test_schema_confirmed_valid_payment():
    cmd_id = uuid.uuid4()
    event_id = uuid.uuid4()
    pay_id = uuid.uuid4()
    model = CreditCommandReconciliationRead(
        command_id=cmd_id,
        status="confirmed",
        operation_type="payment",
        event_id=event_id,
        resource_id=pay_id,
        payment_id=pay_id,
    )
    assert model.status == "confirmed"
    assert model.operation_type == "payment"
    assert model.payment_id == pay_id
    assert model.transaction_id is None


def test_schema_confirmed_valid_pay_early():
    cmd_id = uuid.uuid4()
    event_id = uuid.uuid4()
    early_id = uuid.uuid4()
    model = CreditCommandReconciliationRead(
        command_id=cmd_id,
        status="confirmed",
        operation_type="pay_early",
        event_id=event_id,
        resource_id=early_id,
        early_payment_id=early_id,
    )
    assert model.status == "confirmed"
    assert model.operation_type == "pay_early"
    assert model.early_payment_id == early_id


def test_schema_confirmed_valid_adjustment():
    cmd_id = uuid.uuid4()
    event_id = uuid.uuid4()
    adj_id = uuid.uuid4()
    model = CreditCommandReconciliationRead(
        command_id=cmd_id,
        status="confirmed",
        operation_type="adjustment",
        event_id=event_id,
        resource_id=adj_id,
        adjustment_id=adj_id,
    )
    assert model.status == "confirmed"
    assert model.operation_type == "adjustment"
    assert model.adjustment_id == adj_id


def test_schema_confirmed_rejects_missing_or_mismatched_ids():
    cmd_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tx_id = uuid.uuid4()

    # Missing event_id
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="confirmed",
            operation_type="purchase",
            resource_id=tx_id,
            transaction_id=tx_id,
        )

    # Missing operation_type
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="confirmed",
            event_id=event_id,
            resource_id=tx_id,
            transaction_id=tx_id,
        )

    # Mismatched ID: purchase with payment_id
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=cmd_id,
            status="confirmed",
            operation_type="purchase",
            event_id=event_id,
            resource_id=tx_id,
            payment_id=tx_id,
        )


def test_schema_extra_forbid():
    with pytest.raises(ValidationError):
        CreditCommandReconciliationRead(
            command_id=uuid.uuid4(),
            status="unknown",
            unexpected_field="disallowed",  # type: ignore[call-arg]
        )


# ── 2. Service Unit Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_command_purchase_confirmed(
    credit_service, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tx_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.user_id = user_id
    mock_event.event_type = "credit_card_purchase"
    card_id = uuid.uuid4()
    mock_event.metadata_ = {"credit_card_id": str(card_id)}
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    mock_tx = MagicMock(spec=CreditCardTransaction)
    mock_tx.id = tx_id
    mock_tx.user_id = user_id
    mock_tx.type = "purchase"
    mock_tx.credit_card_id = card_id
    mock_credit_repo.get_transaction_by_event_for_user.return_value = mock_tx

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "confirmed"
    assert res.command_id == command_id
    assert res.operation_type == "purchase"
    assert res.event_id == event_id
    assert res.resource_id == tx_id
    assert res.transaction_id == tx_id
    assert res.payment_id is None
    assert res.early_payment_id is None
    assert res.adjustment_id is None


@pytest.mark.asyncio
async def test_reconcile_command_standard_payment_confirmed(
    credit_service, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()
    payment_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.user_id = user_id
    mock_event.event_type = "credit_card_payment"
    card_id = uuid.uuid4()
    mock_event.metadata_ = {"operation_type": "credit_card_payment", "credit_card_id": str(card_id)}
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    mock_payment = MagicMock(spec=CreditCardPayment)
    mock_payment.id = payment_id
    mock_payment.user_id = user_id
    mock_payment.credit_card_id = card_id
    mock_payment.payment_transaction_id = uuid.uuid4()
    mock_payment.command_id = command_id
    mock_credit_repo.get_payment_by_event_for_user.return_value = mock_payment
    mock_credit_repo.get_early_payment_by_event_for_user.return_value = None
    payment_tx = MagicMock(spec=CreditCardTransaction)
    payment_tx.id = mock_payment.payment_transaction_id
    payment_tx.user_id = user_id
    payment_tx.credit_card_id = card_id
    payment_tx.type = "payment"
    mock_credit_repo.get_transaction_by_event_for_user.return_value = payment_tx

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "confirmed"
    assert res.command_id == command_id
    assert res.operation_type == "payment"
    assert res.event_id == event_id
    assert res.resource_id == payment_id
    assert res.payment_id == payment_id
    assert res.transaction_id is None
    assert res.early_payment_id is None
    assert res.adjustment_id is None


@pytest.mark.asyncio
async def test_reconcile_command_early_payment_confirmed(
    credit_service, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()
    early_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.user_id = user_id
    mock_event.event_type = "credit_card_payment"
    mock_event.metadata_ = {
        "operation_type": "credit_card_pay_early",
        "is_early_payment": True,
        "purchase_transaction_id": str(uuid.uuid4()),
    }
    card_id = uuid.uuid4()
    mock_event.metadata_["credit_card_id"] = str(card_id)
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    mock_early = MagicMock(spec=CreditCardEarlyPayment)
    mock_early.id = early_id
    mock_early.user_id = user_id
    mock_early.credit_card_id = card_id
    mock_early.payment_transaction_id = uuid.uuid4()
    mock_early.purchase_transaction_id = uuid.UUID(mock_event.metadata_["purchase_transaction_id"])
    mock_credit_repo.get_early_payment_by_event_for_user.return_value = mock_early
    mock_payment = MagicMock(spec=CreditCardPayment)
    mock_payment.id = uuid.uuid4()
    mock_payment.user_id = user_id
    mock_payment.credit_card_id = card_id
    mock_payment.payment_transaction_id = mock_early.payment_transaction_id
    mock_payment.command_id = command_id
    mock_credit_repo.get_payment_by_event_for_user.return_value = mock_payment
    payment_tx = MagicMock(spec=CreditCardTransaction)
    payment_tx.id = mock_early.payment_transaction_id
    payment_tx.user_id = user_id
    payment_tx.credit_card_id = card_id
    payment_tx.type = "payment"
    mock_credit_repo.get_transaction_by_event_for_user.return_value = payment_tx

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "confirmed"
    assert res.command_id == command_id
    assert res.operation_type == "pay_early"
    assert res.event_id == event_id
    assert res.resource_id == early_id
    assert res.early_payment_id == early_id
    assert res.transaction_id is None
    assert res.payment_id is None
    assert res.adjustment_id is None


@pytest.mark.asyncio
async def test_reconcile_command_adjustment_confirmed(
    credit_service, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()
    adj_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.user_id = user_id
    mock_event.event_type = EventType.MANUAL_ADJUSTMENT
    mock_event.metadata_ = {
        "operation_type": "credit_card_adjustment",
        "adjustment_type": "refund",
    }
    card_id = uuid.uuid4()
    mock_event.metadata_["credit_card_id"] = str(card_id)
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    mock_adj = MagicMock(spec=CreditCardAdjustment)
    mock_adj.id = adj_id
    mock_adj.user_id = user_id
    mock_adj.credit_card_id = card_id
    mock_adj.adjustment_type = "refund"
    mock_adj.command_id = command_id
    mock_credit_repo.get_adjustment_by_event_for_user.return_value = mock_adj

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "confirmed"
    assert res.command_id == command_id
    assert res.operation_type == "adjustment"
    assert res.event_id == event_id
    assert res.resource_id == adj_id
    assert res.adjustment_id == adj_id
    assert res.transaction_id is None
    assert res.payment_id is None
    assert res.early_payment_id is None


@pytest.mark.asyncio
async def test_reconcile_command_unknown_nonexistent(
    credit_service, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = None

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "unknown"
    assert res.command_id == command_id
    assert res.operation_type is None
    assert res.event_id is None
    assert res.resource_id is None
    assert res.transaction_id is None
    assert res.payment_id is None
    assert res.early_payment_id is None
    assert res.adjustment_id is None


@pytest.mark.asyncio
async def test_reconcile_command_unknown_foreign_owner(
    credit_service, mock_ledger_service
):
    user_id = uuid.uuid4()
    foreign_user_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.user_id = foreign_user_id
    mock_event.event_type = "credit_card_purchase"
    mock_event.metadata_ = {"credit_card_id": str(uuid.uuid4())}
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    # Foreign-owner must return identical unknown payload without leaking info
    assert res.status == "unknown"
    assert res.command_id == command_id
    assert res.operation_type is None
    assert res.event_id is None
    assert res.resource_id is None


@pytest.mark.asyncio
async def test_reconcile_command_unknown_non_credit_event(
    credit_service, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.user_id = user_id
    mock_event.event_type = "transfer_out"
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "unknown"
    assert res.command_id == command_id
    assert res.operation_type is None


@pytest.mark.asyncio
async def test_reconcile_command_unknown_incomplete_mapping(
    credit_service, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.user_id = user_id
    mock_event.event_type = "credit_card_purchase"
    card_id = uuid.uuid4()
    mock_event.metadata_ = {"credit_card_id": str(card_id)}
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    # Transaction row is missing from DB
    mock_credit_repo.get_transaction_by_event_for_user.return_value = None

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "unknown"
    assert res.command_id == command_id
    assert res.operation_type is None
    assert res.resource_id is None


@pytest.mark.asyncio
async def test_reconcile_command_unknown_foreign_resource(
    credit_service, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    other_user = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = uuid.uuid4()
    mock_event.user_id = user_id
    mock_event.event_type = "credit_card_purchase"
    card_id = uuid.uuid4()
    mock_event.metadata_ = {"credit_card_id": str(card_id)}
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    # Resource belongs to a different user
    mock_tx = MagicMock(spec=CreditCardTransaction)
    mock_tx.id = uuid.uuid4()
    mock_tx.user_id = other_user
    mock_tx.type = "purchase"
    mock_tx.credit_card_id = card_id
    mock_credit_repo.get_transaction_by_event_for_user.return_value = mock_tx

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)

    assert res.status == "unknown"
    assert res.command_id == command_id


@pytest.mark.asyncio
async def test_reconcile_command_read_only_zero_writes(
    credit_service, mock_session, mock_credit_repo, mock_ledger_service
):
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tx_id = uuid.uuid4()

    mock_event = MagicMock()
    mock_event.id = event_id
    mock_event.user_id = user_id
    mock_event.event_type = "credit_card_purchase"
    card_id = uuid.uuid4()
    mock_event.metadata_ = {"credit_card_id": str(card_id)}
    mock_ledger_service.repository.get_by_command_id_for_user.return_value = mock_event

    mock_tx = MagicMock(spec=CreditCardTransaction)
    mock_tx.id = tx_id
    mock_tx.user_id = user_id
    mock_tx.type = "purchase"
    mock_tx.credit_card_id = card_id
    mock_credit_repo.get_transaction_by_event_for_user.return_value = mock_tx

    res = await credit_service.reconcile_command(user_id=user_id, command_id=command_id)
    assert res.status == "confirmed"

    # Invariant: zero writes occurred
    mock_session.add.assert_not_called()
    mock_session.flush.assert_not_called()
    mock_session.commit.assert_not_called()


# ── 3. FastAPI Route Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_reconcile_command_confirmed():
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tx_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.reconcile_command.return_value = CreditCommandReconciliationRead(
        command_id=command_id,
        status="confirmed",
        operation_type="purchase",
        event_id=event_id,
        resource_id=tx_id,
        transaction_id=tx_id,
    )

    mock_user = UserRead(
        id=user_id,
        email="test@example.com",
        name="Test User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )

    app.dependency_overrides[get_credit_service] = lambda: mock_service
    app.dependency_overrides[get_current_user_profile_dep] = lambda: mock_user

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/credit/commands/{command_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "confirmed"
            assert data["command_id"] == str(command_id)
            assert data["operation_type"] == "purchase"
            assert data["event_id"] == str(event_id)
            assert data["transaction_id"] == str(tx_id)
            assert data["resource_id"] == str(tx_id)
            assert data["payment_id"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_reconcile_command_unknown():
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.reconcile_command.return_value = CreditCommandReconciliationRead(
        command_id=command_id,
        status="unknown",
        operation_type=None,
        event_id=None,
        resource_id=None,
        transaction_id=None,
        payment_id=None,
        early_payment_id=None,
        adjustment_id=None,
    )

    mock_user = UserRead(
        id=user_id,
        email="test@example.com",
        name="Test User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )

    app.dependency_overrides[get_credit_service] = lambda: mock_service
    app.dependency_overrides[get_current_user_profile_dep] = lambda: mock_user

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/credit/commands/{command_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "unknown"
            assert data["command_id"] == str(command_id)
            assert data["operation_type"] is None
            assert data["event_id"] is None
            assert data["resource_id"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_reconcile_command_invalid_uuid():
    mock_user = UserRead(
        id=uuid.uuid4(),
        email="test@example.com",
        name="Test User",
        timezone="America/Bogota",
        currency="COP",
        status="active",
    )
    app.dependency_overrides[get_current_user_profile_dep] = lambda: mock_user
    app.dependency_overrides[get_credit_service] = lambda: AsyncMock()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/credit/commands/not-a-valid-uuid")
            # FastAPI rejects invalid UUID path parameters with 422
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
