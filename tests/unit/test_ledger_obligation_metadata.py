"""
tests/unit/test_ledger_obligation_metadata.py
=============================================
Pruebas focalizadas para el enriquecimiento aditivo de metadata de obligaciones
en LedgerEventDetail y LedgerRepository:
a) Evento de obligación devuelve obligation_id y obligation_name autoritativos.
b) Evento no relacionado devuelve ambos campos como None/null coherente sin queries extra.
c) Evento histórico de obligación cuya relación no es resoluble no falla y devuelve None coherente.
d) Fila de outer join con Obligation ausente devuelve obligation_id presente y obligation_name None.
"""

import asyncio
import sys
import uuid
import warnings
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.ledger.models import FinancialEvent
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventDetail

# En Windows (Python 3.8+), ProactorEventLoop puede bloquearse inicializando self-pipe
# sockets bajo ciertos runners de prueba. Configuramos selector explícito para win32.
if sys.platform == "win32":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy_cls is not None:
            asyncio.set_event_loop_policy(policy_cls())


def _run_sync(coro: Any) -> Any:
    """Ejecuta una corrutina usando SelectorEventLoop en win32 para evitar self-pipe hangs."""
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return asyncio.run(coro)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.execute_count = 0
        self.last_stmt = None

    async def execute(self, stmt: Any) -> FakeResult:
        self.execute_count += 1
        self.last_stmt = stmt
        return FakeResult(self.rows)


def test_ledger_event_detail_schema_obligation_metadata_resolved() -> None:
    """Caso a (Schema): Evento con relación a obligación incluye id y nombre."""
    ob_id = uuid.uuid4()
    detail = LedgerEventDetail(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("150000.00"),
        currency="COP",
        description="Pago arriendo",
        source="backend",
        created_at=datetime.now(UTC),
        obligation_id=ob_id,
        obligation_name="Arriendo Apartamento",
    )
    assert detail.obligation_id == ob_id
    assert detail.obligation_name == "Arriendo Apartamento"
    dumped = detail.model_dump(mode="json")
    assert dumped["obligation_id"] == str(ob_id)
    assert dumped["obligation_name"] == "Arriendo Apartamento"


def test_ledger_event_detail_schema_unrelated_event_defaults_to_none() -> None:
    """Caso b (Schema): Evento no relacionado devuelve ambos campos null/None."""
    detail = LedgerEventDetail(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="income",
        direction="inflow",
        amount=Decimal("5000000.00"),
        currency="COP",
        description="Salario",
        source="backend",
        created_at=datetime.now(UTC),
    )
    assert detail.obligation_id is None
    assert detail.obligation_name is None
    dumped = detail.model_dump(mode="json")
    assert dumped["obligation_id"] is None
    assert dumped["obligation_name"] is None


def test_ledger_event_detail_from_attributes_with_financial_event() -> None:
    """Caso c (Schema): FinancialEvent sin resolver o histórico no falla al validar."""
    event = FinancialEvent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("20000.00"),
        currency="COP",
        description="Pago histórico",
        source="backend",
        period="2026-01",
        created_at=datetime.now(UTC),
    )
    assert event.obligation_id is None
    assert event.obligation_name is None

    detail = LedgerEventDetail.model_validate(event)
    assert detail.obligation_id is None
    assert detail.obligation_name is None


def test_repository_enrich_obligation_metadata_resolved() -> None:
    """Caso a (Repo): Evento de obligación resuelve id y nombre autoritativos sin N+1."""
    event_id = uuid.uuid4()
    obligation_id = uuid.uuid4()
    obligation_name = "Plan Telefonía Móvil"

    event = FinancialEvent(
        id=event_id,
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("45000.00"),
        currency="COP",
        source="backend",
        period="2026-10",
        created_at=datetime.now(UTC),
    )

    fake_session = FakeSession(rows=[(event_id, obligation_id, obligation_name)])
    repo = LedgerRepository(fake_session)

    _run_sync(repo._enrich_obligation_metadata([event]))

    assert event.obligation_id == obligation_id
    assert event.obligation_name == obligation_name
    assert fake_session.execute_count == 1
    assert fake_session.last_stmt is not None
    assert "LEFT OUTER JOIN obligations" in str(fake_session.last_stmt)

    detail = LedgerEventDetail.model_validate(event)
    assert detail.obligation_id == obligation_id
    assert detail.obligation_name == obligation_name


def test_repository_enrich_obligation_metadata_unrelated_skips_query() -> None:
    """Caso b (Repo): Evento no relacionado no ejecuta query extra y devuelve None."""
    event = FinancialEvent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="income",
        direction="inflow",
        amount=Decimal("1000000.00"),
        currency="COP",
        source="backend",
        period="2026-10",
        created_at=datetime.now(UTC),
    )

    fake_session = FakeSession()
    repo = LedgerRepository(fake_session)

    _run_sync(repo._enrich_obligation_metadata([event]))

    assert event.obligation_id is None
    assert event.obligation_name is None
    assert fake_session.execute_count == 0


def test_repository_enrich_obligation_metadata_unresolvable_historical() -> None:
    """Caso c (Repo): Evento histórico de tipo obligation_payment no resoluble no falla."""
    event_id = uuid.uuid4()
    event = FinancialEvent(
        id=event_id,
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("30000.00"),
        currency="COP",
        source="backend",
        period="2025-05",
        created_at=datetime.now(UTC),
    )

    fake_session = FakeSession(rows=[])
    repo = LedgerRepository(fake_session)

    _run_sync(repo._enrich_obligation_metadata([event]))

    assert event.obligation_id is None
    assert event.obligation_name is None
    assert fake_session.execute_count == 1

    detail = LedgerEventDetail.model_validate(event)
    assert detail.obligation_id is None
    assert detail.obligation_name is None


def test_repository_enrich_obligation_metadata_outerjoin_missing_obligation() -> None:
    """Caso d (Repo outerjoin): ObligationPayment existe pero Obligation está ausente.

    Debe resultar en obligation_id presente y obligation_name null/None.
    """
    event_id = uuid.uuid4()
    obligation_id = uuid.uuid4()

    event = FinancialEvent(
        id=event_id,
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("50000.00"),
        currency="COP",
        source="backend",
        period="2026-05",
        created_at=datetime.now(UTC),
    )

    fake_session = FakeSession(rows=[(event_id, obligation_id, None)])
    repo = LedgerRepository(fake_session)

    _run_sync(repo._enrich_obligation_metadata([event]))

    assert event.obligation_id == obligation_id
    assert event.obligation_name is None
    assert fake_session.execute_count == 1

    detail = LedgerEventDetail.model_validate(event)
    assert detail.obligation_id == obligation_id
    assert detail.obligation_name is None
    dumped = detail.model_dump(mode="json")
    assert dumped["obligation_id"] == str(obligation_id)
    assert dumped["obligation_name"] is None


def test_repository_enrich_obligation_metadata_with_async_mock() -> None:
    """Valida compatibilidad directa con AsyncMock sin bloqueos por configuración de mappers."""
    from unittest.mock import AsyncMock, MagicMock

    event_id = uuid.uuid4()
    ob_id = uuid.uuid4()
    event = FinancialEvent(
        id=event_id,
        user_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_type="obligation_payment",
        direction="outflow",
        amount=Decimal("10000.00"),
        currency="COP",
        source="backend",
        created_at=datetime.now(UTC),
    )
    mock_res = MagicMock()
    mock_res.all.return_value = [(event_id, ob_id, "Servicio Energía")]
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_res

    repo = LedgerRepository(mock_session)
    _run_sync(repo._enrich_obligation_metadata([event]))

    assert event.obligation_id == ob_id
    assert event.obligation_name == "Servicio Energía"
    assert mock_session.execute.await_count == 1
