"""
tests/unit/test_ledger.py
=========================
Pruebas unitarias para el dominio Ledger. No conecta con BD real.
Valida:
- Pydantic schemas (amount positivo, enums válidos, defaults).
- Cálculo del period helper en zona horaria America/Bogota.
- Manejo de UoW (commit, rollback explícito).
- Repository simulando validaciones de ownership y captura de IntegrityError.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import ForbiddenError
from app.core.uow import UnitOfWork
from app.ledger.enums import Direction, EventType
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate, LedgerEventRead
from app.ledger.service import generate_period_for_bogota

# ── Schemas y Pydantic ────────────────────────────────────────────────────────


def test_ledger_schema_valid_amount() -> None:
    """amount positivo es aceptado."""
    event = LedgerEventCreate(
        user_id=uuid.uuid4(),
        event_type=EventType.INCOME,
        direction=Direction.INFLOW,
        amount=Decimal("100.50"),
    )
    assert event.amount == Decimal("100.50")


def test_ledger_schema_preserves_precision() -> None:
    """Valida la precisión de decimales."""
    event = LedgerEventCreate(
        user_id=uuid.uuid4(),
        event_type=EventType.INCOME,
        direction=Direction.INFLOW,
        amount=Decimal("10000.99"),
    )
    assert event.amount == Decimal("10000.99")


def test_ledger_schema_rejects_zero_amount() -> None:
    """amount cero es rechazado antes de BD."""
    with pytest.raises(ValidationError) as exc_info:
        LedgerEventCreate(
            user_id=uuid.uuid4(),
            event_type=EventType.EXPENSE,
            direction=Direction.OUTFLOW,
            amount=Decimal("0"),
        )
    assert exc_info.value.error_count() > 0


def test_ledger_schema_rejects_negative_amount() -> None:
    """amount negativo es rechazado antes de BD."""
    with pytest.raises(ValidationError) as exc_info:
        LedgerEventCreate(
            user_id=uuid.uuid4(),
            event_type=EventType.EXPENSE,
            direction=Direction.OUTFLOW,
            amount=Decimal("-50"),
        )
    assert exc_info.value.error_count() > 0


def test_ledger_schema_valid_enums() -> None:
    """event_type y direction aceptan solo valores permitidos."""
    with pytest.raises(ValidationError):
        LedgerEventCreate(
            user_id=uuid.uuid4(),
            event_type="invalid_type",  # type: ignore[arg-type]
            direction=Direction.INFLOW,
            amount=Decimal("100"),
        )

    with pytest.raises(ValidationError):
        LedgerEventCreate(
            user_id=uuid.uuid4(),
            event_type=EventType.INCOME,
            direction="upward",  # type: ignore[arg-type]
            amount=Decimal("100"),
        )


def test_ledger_schema_raw_message_not_exposed_in_read() -> None:
    """raw_message existe en Create pero no en Read."""
    create = LedgerEventCreate(
        user_id=uuid.uuid4(),
        event_type=EventType.MANUAL_ADJUSTMENT,
        direction=Direction.NEUTRAL,
        amount=Decimal("10"),
        raw_message="Sensitive payload",
    )
    assert create.raw_message == "Sensitive payload"

    # Simular modelo DB
    db_obj = MagicMock()
    db_obj.id = uuid.uuid4()
    db_obj.user_id = create.user_id
    db_obj.account_id = None
    db_obj.category_id = None
    db_obj.event_type = create.event_type
    db_obj.direction = create.direction
    db_obj.amount = create.amount
    db_obj.currency = "COP"
    db_obj.description = None
    # raw_message se omite simulando lectura segura
    db_obj.source = "backend"
    db_obj.occurred_at = datetime.now(UTC)
    db_obj.period = "2026-06"
    db_obj.metadata_ = {}
    db_obj.created_at = datetime.now(UTC)
    db_obj.command_id = None

    read = LedgerEventRead.model_validate(db_obj)
    assert not hasattr(read, "raw_message")


def test_ledger_schema_default_metadata_is_empty_dict() -> None:
    """metadata inicia como diccionario vacío."""
    create = LedgerEventCreate(
        user_id=uuid.uuid4(),
        event_type=EventType.INCOME,
        direction=Direction.INFLOW,
        amount=Decimal("10"),
    )
    assert create.metadata == {}


def test_ledger_schema_omits_period() -> None:
    """period no existe en el payload de creación para evitar ambigüedad."""
    create = LedgerEventCreate(
        user_id=uuid.uuid4(),
        event_type=EventType.INCOME,
        direction=Direction.INFLOW,
        amount=Decimal("10"),
    )
    assert not hasattr(create, "period")


# ── Helpers y Service ─────────────────────────────────────────────────────────


def test_generate_period_for_bogota() -> None:
    """El helper de period calcula siempre YYYY-MM en hora de Bogotá."""
    # Instante en UTC que ya es del día siguiente respecto a Bogotá
    # Ej: 2026-07-01 03:00:00 UTC es 2026-06-30 22:00:00 en Bogotá.
    dt_utc = datetime(2026, 7, 1, 3, 0, 0, tzinfo=UTC)
    period = generate_period_for_bogota(dt_utc)
    assert period == "2026-06"


# ── Unit of Work ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uow_commit_on_success() -> None:
    """UoW llama a commit si el context manager finaliza sin error."""
    mock_session = AsyncMock()
    uow = UnitOfWork(session=mock_session)

    async with uow.transaction():
        pass

    mock_session.commit.assert_awaited_once()
    mock_session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_uow_rollback_on_exception() -> None:
    """UoW llama a rollback y propaga excepción si hay error."""
    mock_session = AsyncMock()
    uow = UnitOfWork(session=mock_session)

    class TestError(Exception):
        pass

    with pytest.raises(TestError):
        async with uow.transaction():
            raise TestError("Forzado")

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()


# ── Repository Ownership Mock ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repository_account_ownership_valid() -> None:
    """Acepta cuenta si el user_id de la DB coincide con el del evento."""
    user_id = uuid.uuid4()
    account_id = uuid.uuid4()

    mock_session = AsyncMock()
    # Simular result.fetchone() retornando el tupla con user_id
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (user_id,)
    mock_session.execute.return_value = mock_result

    repo = LedgerRepository(mock_session)
    await repo.check_account_ownership(account_id, user_id)
    # No lanza excepción -> Test pasa


@pytest.mark.asyncio
async def test_repository_account_ownership_rejects_global_or_null() -> None:
    """Rechaza cuenta si el user_id es NULL (no apta para transaccionar liquidez)."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (None,)
    mock_session.execute.return_value = mock_result

    repo = LedgerRepository(mock_session)
    with pytest.raises(ForbiddenError) as exc_info:
        await repo.check_account_ownership(uuid.uuid4(), uuid.uuid4())
    assert "Cuentas globales no permitidas" in exc_info.value.message


@pytest.mark.asyncio
async def test_repository_account_ownership_rejects_other_user() -> None:
    """Rechaza cuenta privada de otro usuario."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (uuid.uuid4(),)
    mock_session.execute.return_value = mock_result

    repo = LedgerRepository(mock_session)
    with pytest.raises(ForbiddenError) as exc_info:
        await repo.check_account_ownership(uuid.uuid4(), uuid.uuid4())
    assert "No tienes permisos" in exc_info.value.message


@pytest.mark.asyncio
async def test_repository_category_ownership_accepts_global() -> None:
    """Las categorías sí pueden tener user_id = NULL y deben aceptarse."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (None,)
    mock_session.execute.return_value = mock_result

    repo = LedgerRepository(mock_session)
    await repo.check_category_ownership(uuid.uuid4(), uuid.uuid4())
    # No lanza excepción -> Test pasa


# ── Repository Idempotency Mock ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repository_handles_command_id_integrity_error() -> None:
    """Atrapa IntegrityError en command_id y lo trata como idempotencia."""
    user_id = uuid.uuid4()
    command_id = uuid.uuid4()
    event_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.add = MagicMock()  # add es sincrono

    # 1. Simular que el flush lanza IntegrityError
    class MockOrig:
        def __init__(self) -> None:
            self.constraint_name = "idx_financial_events_command_id"

        def __str__(self) -> str:
            return "duplicate key value violates unique constraint"

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    mock_session.begin_nested = MagicMock(return_value=AsyncContextManagerMock())
    mock_session.flush.side_effect = IntegrityError("msg", "params", MockOrig())

    # 2. Simular que al buscar el existente sí lo encuentra
    mock_db_event = MagicMock()
    mock_db_event.id = event_id
    mock_db_event.user_id = user_id
    mock_db_event.account_id = None
    mock_db_event.category_id = None
    mock_db_event.event_type = EventType.INCOME
    mock_db_event.direction = Direction.INFLOW
    mock_db_event.amount = Decimal("100")
    mock_db_event.currency = "COP"
    mock_db_event.description = None
    mock_db_event.source = "test"
    mock_db_event.occurred_at = datetime.now()
    mock_db_event.period = "2026-06"
    mock_db_event.metadata_ = {}
    mock_db_event.created_at = datetime.now()
    mock_db_event.command_id = command_id

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_db_event
    mock_session.execute.return_value = mock_result

    repo = LedgerRepository(mock_session)

    # Desactivar ownership check para no complicar el mock
    repo.check_account_ownership = AsyncMock()  # type: ignore[method-assign]
    repo.check_category_ownership = AsyncMock()  # type: ignore[method-assign]

    create = LedgerEventCreate(
        user_id=user_id,
        event_type=EventType.INCOME,
        direction=Direction.INFLOW,
        amount=Decimal("100"),
        command_id=command_id,
    )

    result = await repo.insert_event(create)
    assert result.idempotent is True
    assert result.event.id == event_id


@pytest.mark.asyncio
async def test_repository_propagates_other_integrity_errors() -> None:
    """Cualquier otro IntegrityError (ej. Foreign Key) se propaga."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    class MockOrig:
        def __init__(self) -> None:
            self.constraint_name = "financial_events_user_id_fkey"

        def __str__(self) -> str:
            return "violates foreign key constraint"

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    mock_session.begin_nested = MagicMock(return_value=AsyncContextManagerMock())
    mock_session.flush.side_effect = IntegrityError("msg", "params", MockOrig())
    repo = LedgerRepository(mock_session)

    create = LedgerEventCreate(
        user_id=uuid.uuid4(),
        event_type=EventType.INCOME,
        direction=Direction.INFLOW,
        amount=Decimal("100"),
    )

    with pytest.raises(IntegrityError):
        await repo.insert_event(create)
