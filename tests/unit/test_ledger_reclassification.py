from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.categories.models import Category
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.errors import ValidationError as AppValidationError
from app.ledger.models import FinancialEvent, FinancialEventReclassification
from app.ledger.schemas import ReclassificationRequest
from app.ledger.service import LedgerService


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.session = AsyncMock()
    repo.session.begin_nested = MagicMock()
    repo.session.begin_nested.return_value.__aenter__ = AsyncMock()
    repo.session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    repo.session.flush = AsyncMock()

    repo.get_reclassification_by_command_id = AsyncMock(return_value=None)
    repo.get_event_for_update = AsyncMock()
    repo.get_category_for_update = AsyncMock()

    def side_effect_create(reclass):
        reclass.id = uuid4()
        reclass.created_at = datetime.now(UTC)

    repo.create_reclassification = MagicMock(side_effect=side_effect_create)
    return repo


@pytest.fixture
def service(mock_repo):
    return LedgerService(mock_repo)


# 1. Evento inexistente -> 404
@pytest.mark.asyncio
async def test_event_not_found(service, mock_repo):
    mock_repo.get_event_for_update.return_value = None
    with pytest.raises(NotFoundError) as exc:
        await service.reclassify_event(
            uuid4(),
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )
    assert exc.value.error_code == "event_not_found"
    mock_repo.get_reclassification_by_command_id.assert_not_called()


# 2. Evento ajeno -> En get_event_for_update devuelve None si user_id no hace match.
@pytest.mark.asyncio
async def test_event_other_user(service, mock_repo):
    mock_repo.get_event_for_update.return_value = None
    with pytest.raises(NotFoundError):
        await service.reclassify_event(
            uuid4(),
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )


# 3. Evento ajeno + command_id existente de otro usuario -> 404 (el lookup global de idempotencia no ocurre antes)
@pytest.mark.asyncio
async def test_event_other_user_existing_command_id(service, mock_repo):
    mock_repo.get_event_for_update.return_value = None
    with pytest.raises(NotFoundError):
        await service.reclassify_event(
            uuid4(),
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )
    mock_repo.get_reclassification_by_command_id.assert_not_called()


# Categorias
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cat_type,is_global,expected_status",
    [
        ("expense", True, "reclassified"),  # 5. Global expense aceptada (user_id=None)
        ("expense", False, "reclassified"),  # 6. Privada propia expense
        ("income", True, "reclassified"),  # 7. Global income
    ],
)
async def test_category_accepted(service, mock_repo, cat_type, is_global, expected_status):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    old_cat_id = uuid4()

    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=old_cat_id, event_type=cat_type
    )
    mock_repo.get_event_for_update.return_value = mock_event

    mock_cat = Category(
        id=new_cat_id, user_id=None if is_global else user_id, type=cat_type, is_active=True
    )
    mock_repo.get_category_for_update.return_value = mock_cat

    req = ReclassificationRequest(new_category_id=new_cat_id, idempotency_key=uuid4())
    res = await service.reclassify_event(user_id, event_id, req)
    assert res.status == expected_status


# 8. Privada ajena -> 403
@pytest.mark.asyncio
async def test_category_other_user(service, mock_repo):
    user_id = uuid4()
    mock_event = FinancialEvent(
        id=uuid4(), user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_cat = Category(id=uuid4(), user_id=uuid4(), type="expense", is_active=True)
    mock_repo.get_category_for_update.return_value = mock_cat
    with pytest.raises(ForbiddenError) as exc:
        await service.reclassify_event(
            user_id,
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )
    assert exc.value.error_code == "category_forbidden"


# 9. Inexistente -> 404
@pytest.mark.asyncio
async def test_category_not_found(service, mock_repo):
    user_id = uuid4()
    mock_event = FinancialEvent(
        id=uuid4(), user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_repo.get_category_for_update.return_value = None
    with pytest.raises(NotFoundError) as exc:
        await service.reclassify_event(
            user_id,
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )
    assert exc.value.error_code == "category_not_found"


# 10. Inactiva -> 422
@pytest.mark.asyncio
async def test_category_inactive(service, mock_repo):
    user_id = uuid4()
    mock_event = FinancialEvent(
        id=uuid4(), user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_cat = Category(id=uuid4(), user_id=user_id, type="expense", is_active=False)
    mock_repo.get_category_for_update.return_value = mock_cat
    with pytest.raises(AppValidationError) as exc:
        await service.reclassify_event(
            user_id,
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )
    assert exc.value.error_code == "category_inactive"


# 11, 12. Type mismatch / Internal (que tambien seria type mismatch)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type, cat_type", [("expense", "income"), ("income", "expense"), ("expense", "transfer")]
)
async def test_category_type_mismatch(service, mock_repo, event_type, cat_type):
    user_id = uuid4()
    mock_event = FinancialEvent(
        id=uuid4(), user_id=user_id, category_id=uuid4(), event_type=event_type
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_cat = Category(id=uuid4(), user_id=user_id, type=cat_type, is_active=True)
    mock_repo.get_category_for_update.return_value = mock_cat
    with pytest.raises(AppValidationError) as exc:
        await service.reclassify_event(
            user_id,
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )
    assert exc.value.error_code == "category_type_mismatch"


# Allowlist
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "goal_contribution",
        "obligation_payment",
        "credit_card_purchase",
        "credit_card_payment",
        "transfer_out",
        "transfer_in",
        "manual_adjustment",
        "opening_balance",
        "balance_adjustment",
        "unknown",
    ],
)
async def test_allowlist_rejected(service, mock_repo, event_type):
    user_id = uuid4()
    mock_event = FinancialEvent(
        id=uuid4(), user_id=user_id, category_id=uuid4(), event_type=event_type
    )
    mock_repo.get_event_for_update.return_value = mock_event

    with pytest.raises(AppValidationError) as exc:
        await service.reclassify_event(
            user_id,
            uuid4(),
            ReclassificationRequest(new_category_id=uuid4(), idempotency_key=uuid4()),
        )

    assert exc.value.error_code == "event_not_reclassifiable"
    mock_repo.create_reclassification.assert_not_called()
    assert mock_repo.session.flush.call_count == 0


# Reason
@pytest.mark.asyncio
async def test_reason_strip_and_whitespace(service, mock_repo):
    user_id = uuid4()
    event_id = uuid4()
    cat_id = uuid4()

    # Whitespace only
    req_ws = ReclassificationRequest(new_category_id=cat_id, idempotency_key=uuid4(), reason="   ")
    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_repo.get_category_for_update.return_value = Category(
        id=cat_id, user_id=user_id, type="expense", is_active=True
    )

    res = await service.reclassify_event(user_id, event_id, req_ws)
    assert res.reason is None

    # Strip
    req_str = ReclassificationRequest(
        new_category_id=uuid4(), idempotency_key=uuid4(), reason="  hola  "
    )
    mock_cat2 = Category(
        id=req_str.new_category_id, user_id=user_id, type="expense", is_active=True
    )
    mock_repo.get_category_for_update.return_value = mock_cat2
    res_str = await service.reclassify_event(user_id, event_id, req_str)
    assert res_str.reason == "hola"


# Idempotencia secuencial
@pytest.mark.asyncio
async def test_idempotent_same_key_and_payload(service, mock_repo):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    command_id = uuid4()
    reason = "Test"

    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event

    mock_reclass = FinancialEventReclassification(
        id=uuid4(),
        financial_event_id=event_id,
        previous_category_id=uuid4(),
        new_category_id=new_cat_id,
        reclassified_by=user_id,
        source="manual",
        reason=reason,
        command_id=command_id,
        created_at=datetime.now(UTC),
    )
    mock_repo.get_reclassification_by_command_id.return_value = mock_reclass

    req = ReclassificationRequest(
        new_category_id=new_cat_id, idempotency_key=command_id, reason=reason
    )
    res = await service.reclassify_event(user_id, event_id, req)

    assert res.status == "idempotent_retry"
    assert res.reclassification_id == mock_reclass.id
    mock_repo.create_reclassification.assert_not_called()
    assert mock_event.category_id != new_cat_id  # No hizo update


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diff_field, diff_val",
    [
        ("financial_event_id", uuid4()),
        ("new_category_id", uuid4()),
        ("reclassified_by", uuid4()),
        ("reason", "Diferente"),
        ("source", "system"),
    ],
)
async def test_idempotent_mismatches(service, mock_repo, diff_field, diff_val):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    command_id = uuid4()
    reason = "Test"

    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event

    base_attrs = {
        "financial_event_id": event_id,
        "new_category_id": new_cat_id,
        "reclassified_by": user_id,
        "reason": reason,
        "source": "manual",
    }
    base_attrs[diff_field] = diff_val

    mock_reclass = FinancialEventReclassification(
        id=uuid4(),
        previous_category_id=uuid4(),
        command_id=command_id,
        created_at=datetime.now(UTC),
        **base_attrs,
    )
    mock_repo.get_reclassification_by_command_id.return_value = mock_reclass

    req = ReclassificationRequest(
        new_category_id=new_cat_id, idempotency_key=command_id, reason=reason
    )
    with pytest.raises(ConflictError) as exc:
        await service.reclassify_event(user_id, event_id, req)
    assert exc.value.error_code == "idempotency_key_reused"


# Prioridad tras lock
@pytest.mark.asyncio
async def test_priority_after_lock(service, mock_repo):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    command_id = uuid4()

    # El evento ya tiene la categoria destino (como si ya se hubiera ejecutado)
    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=new_cat_id, event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event

    # Pero el comando existe y coincide
    mock_reclass = FinancialEventReclassification(
        id=uuid4(),
        financial_event_id=event_id,
        previous_category_id=uuid4(),
        new_category_id=new_cat_id,
        reclassified_by=user_id,
        source="manual",
        reason=None,
        command_id=command_id,
        created_at=datetime.now(UTC),
    )
    mock_repo.get_reclassification_by_command_id.return_value = mock_reclass

    req = ReclassificationRequest(new_category_id=new_cat_id, idempotency_key=command_id)
    res = await service.reclassify_event(user_id, event_id, req)

    assert res.status == "idempotent_retry"
    mock_repo.create_reclassification.assert_not_called()


# IntegrityError
@pytest.mark.asyncio
async def test_integrity_error_recovery(service, mock_repo):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    command_id = uuid4()

    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_cat = Category(id=new_cat_id, user_id=user_id, type="expense", is_active=True)
    mock_repo.get_category_for_update.return_value = mock_cat

    # Simular que al hacer flush lanza IntegrityError por command_id duplicado
    class MockOrig:
        constraint_name = "uq_financial_event_reclassifications_command_id"

    mock_repo.session.flush.side_effect = IntegrityError("", "", MockOrig())

    # La primera vez que busca el command_id no lo encuentra
    # Despues del catch, lo vuelve a buscar y s lo encuentra
    mock_reclass = FinancialEventReclassification(
        id=uuid4(),
        financial_event_id=event_id,
        previous_category_id=uuid4(),
        new_category_id=new_cat_id,
        reclassified_by=user_id,
        source="manual",
        reason=None,
        command_id=command_id,
        created_at=datetime.now(UTC),
    )
    mock_repo.get_reclassification_by_command_id.side_effect = [None, mock_reclass]

    req = ReclassificationRequest(new_category_id=new_cat_id, idempotency_key=command_id)
    res = await service.reclassify_event(user_id, event_id, req)

    assert res.status == "idempotent_retry"
    assert mock_repo.get_reclassification_by_command_id.call_count == 2


@pytest.mark.asyncio
async def test_integrity_error_conflict(service, mock_repo):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    command_id = uuid4()

    mock_event = FinancialEvent(
        id=event_id, user_id=user_id, category_id=uuid4(), event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_cat = Category(id=new_cat_id, user_id=user_id, type="expense", is_active=True)
    mock_repo.get_category_for_update.return_value = mock_cat

    class MockOrig:
        constraint_name = "uq_financial_event_reclassifications_command_id"

    mock_repo.session.flush.side_effect = IntegrityError("", "", MockOrig())

    # Encuentra uno diferente
    mock_reclass = FinancialEventReclassification(
        id=uuid4(),
        financial_event_id=event_id,
        previous_category_id=uuid4(),
        new_category_id=uuid4(),
        reclassified_by=user_id,
        source="manual",
        reason=None,
        command_id=command_id,
        created_at=datetime.now(UTC),
    )
    mock_repo.get_reclassification_by_command_id.side_effect = [None, mock_reclass]

    req = ReclassificationRequest(new_category_id=new_cat_id, idempotency_key=command_id)
    with pytest.raises(ConflictError) as exc:
        await service.reclassify_event(user_id, event_id, req)
    assert exc.value.error_code == "idempotency_key_reused"


# Misma categoria (nueva request)
@pytest.mark.asyncio
async def test_same_category_new_request(service, mock_repo):
    user_id = uuid4()
    cat_id = uuid4()
    mock_event = FinancialEvent(
        id=uuid4(), user_id=user_id, category_id=cat_id, event_type="expense"
    )
    mock_repo.get_event_for_update.return_value = mock_event
    mock_repo.get_reclassification_by_command_id.return_value = None  # No hay idempotencia

    with pytest.raises(ConflictError) as exc:
        await service.reclassify_event(
            user_id,
            uuid4(),
            ReclassificationRequest(new_category_id=cat_id, idempotency_key=uuid4()),
        )
    assert exc.value.error_code == "category_already_assigned"
    mock_repo.create_reclassification.assert_not_called()


# Invariantes
@pytest.mark.asyncio
async def test_invariants_are_preserved(service, mock_repo):
    user_id = uuid4()
    event_id = uuid4()
    new_cat_id = uuid4()
    old_cat_id = uuid4()

    event_data = {
        "id": event_id,
        "user_id": user_id,
        "category_id": old_cat_id,
        "event_type": "expense",
        "amount": 100,
        "account_id": uuid4(),
        "direction": "outflow",
        "currency": "USD",
        "occurred_at": datetime.now(UTC),
        "period": "2026-07",
        "metadata_": {"test": 1},
        "description": "Test",
        "raw_message": "Raw",
        "source": "api",
        "command_id": uuid4(),
        "source_message_id": uuid4(),
        "transfer_id": None,
        "created_at": datetime.now(UTC),
    }

    mock_event = FinancialEvent(**event_data)
    mock_repo.get_event_for_update.return_value = mock_event
    mock_repo.get_category_for_update.return_value = Category(
        id=new_cat_id, user_id=user_id, type="expense", is_active=True
    )

    req = ReclassificationRequest(new_category_id=new_cat_id, idempotency_key=uuid4())
    await service.reclassify_event(user_id, event_id, req)

    assert mock_event.category_id == new_cat_id
    for key, value in event_data.items():
        if key != "category_id":
            assert getattr(mock_event, key) == value
