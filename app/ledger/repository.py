"""
app/ledger/repository.py
========================
Interacción directa con la base de datos para el dominio Ledger.
Maneja operaciones de SQLAlchemy y atrapa IntegrityError para resolver la
idempotencia financiera a nivel de BD (UNIQUE constraint en command_id).

Ownership:
No ejecuta consultas complejas de dominios ajenos, pero sí puede hacer
verificaciones estructurales básicas apoyadas en SQLAlchemy.
"""

from uuid import UUID
from decimal import Decimal
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, InfrastructureError
from app.ledger.models import FinancialEvent
from app.ledger.schemas import LedgerEventCreate, LedgerEventRead, LedgerEventResult


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check_account_ownership(self, account_id: UUID, user_id: UUID) -> None:
        """
        Validación rápida estructural de Ownership.
        Evita inyecciones cruzadas de IDs entre usuarios distintos.
        Un account con user_id NULL se rechaza para escrituras.
        """
        query = text("SELECT user_id FROM accounts WHERE id = :account_id")
        result = await self.session.execute(query, {"account_id": account_id})
        row = result.fetchone()

        if not row:
            raise ForbiddenError(message="Cuenta no encontrada o inaccesible.")

        db_user_id = row[0]
        if db_user_id is None:
            raise ForbiddenError(
                message="Cuentas globales no permitidas para operaciones transaccionales."
            )

        # Validar casteando a str para comparar seguramente (UUID compatibility)
        if str(db_user_id) != str(user_id):
            raise ForbiddenError(message="No tienes permisos sobre esta cuenta.")

    async def check_category_ownership(self, category_id: UUID, user_id: UUID) -> None:
        """
        Igual que account_ownership, pero category sí permite user_id NULL (categorías globales).
        """
        query = text("SELECT user_id FROM categories WHERE id = :category_id")
        result = await self.session.execute(query, {"category_id": category_id})
        row = result.fetchone()

        if not row:
            raise ForbiddenError(message="Categoría no encontrada o inaccesible.")

        db_user_id = row[0]
        # NULL es válido (categoría global compartida).
        if db_user_id is not None and str(db_user_id) != str(user_id):
            raise ForbiddenError(message="No tienes permisos sobre esta categoría privada.")

    async def get_by_command_id(self, command_id: UUID) -> FinancialEvent | None:
        """Busca un evento por su identificador de comando de negocio."""
        stmt = select(FinancialEvent).where(FinancialEvent.command_id == command_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert_event(self, event_data: LedgerEventCreate) -> LedgerEventResult:
        """
        Persiste un evento en la tabla `financial_events`.

        Si el `command_id` es no-nulo y ya existe, Postgres lanza `IntegrityError`
        (violando el unique index parcial `idx_financial_events_command_id`).
        En ese caso, recuperamos el evento y devolvemos idempotencia.
        """
        # 1. Validar ownership estructural primero (si aplican)
        if event_data.account_id:
            await self.check_account_ownership(event_data.account_id, event_data.user_id)
        if event_data.category_id:
            await self.check_category_ownership(event_data.category_id, event_data.user_id)

        # 2. Preparar modelo SQLAlchemy
        db_event = FinancialEvent(
            user_id=event_data.user_id,
            account_id=event_data.account_id,
            category_id=event_data.category_id,
            event_type=event_data.event_type.value,
            direction=event_data.direction.value,
            amount=event_data.amount,
            currency=event_data.currency,
            description=event_data.description,
            raw_message=event_data.raw_message,
            source=event_data.source,
            occurred_at=event_data.occurred_at,
            metadata_=event_data.metadata,
            command_id=event_data.command_id,
            transfer_id=event_data.transfer_id,
            source_message_id=event_data.source_message_id,
        )

        # occurred_at se setea solo si viene, si no delega al server_default (now())
        if event_data.occurred_at:
            db_event.occurred_at = event_data.occurred_at

        # 3. Intentar el flush que dispara el INSERT.
        # En caso de estar usando UoW, flush pre-escribe a BD (sin hacer commit)
        # Usamos begin_nested (Savepoint) para que el IntegrityError
        # no aborte la transacción superior.
        try:
            async with self.session.begin_nested():
                self.session.add(db_event)
                await self.session.flush()
        except IntegrityError as exc:
            # begin_nested() automáticamente hace rollback al savepoint cuando hay excepción.

            constraint_name = getattr(exc.orig, "constraint_name", None)
            error_msg = str(exc.orig)

            # Reconocer idx_financial_events_command_id por nombre directo o búsqueda de texto
            is_idempotency = (
                constraint_name
                in (
                    "idx_financial_events_command_id",
                    "financial_events_command_id_key",
                )
                or "idx_financial_events_command_id" in error_msg
                or "financial_events_command_id_key" in error_msg
            )

            # Solo consideramos idempotencia SI fue específicamente el constraint del command_id
            if is_idempotency:
                # Recuperar el evento original
                assert event_data.command_id is not None
                existing_event = await self.get_by_command_id(event_data.command_id)
                if not existing_event:
                    # Caso muy anómalo: violó el unique pero no se pudo leer.
                    raise InfrastructureError("Error recuperando evento idempotente")

                return LedgerEventResult(
                    event=LedgerEventRead.model_validate(existing_event),
                    idempotent=True,
                )
            else:
                # Cualquier otro IntegrityError (ej. FK inválida) se deja pasar o se relanza
                raise

        # 4. Inserción exitosa (no idempotente)
        return LedgerEventResult(
            event=LedgerEventRead.model_validate(db_event),
            idempotent=False,
        )

    async def list_events(
        self,
        user_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        account_id: UUID | None = None,
        category_id: UUID | None = None,
        event_type: str | None = None,
        direction: str | None = None,
        amount_min: Decimal | float | None = None,
        amount_max: Decimal | float | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[FinancialEvent], int]:
        from sqlalchemy.orm import selectinload
        from sqlalchemy import func, or_

        stmt = select(FinancialEvent).where(FinancialEvent.user_id == user_id)
        
        if date_from:
            stmt = stmt.where(FinancialEvent.occurred_at >= date_from)
        if date_to:
            stmt = stmt.where(FinancialEvent.occurred_at <= date_to)
        if account_id:
            stmt = stmt.where(FinancialEvent.account_id == account_id)
        if category_id:
            stmt = stmt.where(FinancialEvent.category_id == category_id)
        if event_type:
            stmt = stmt.where(FinancialEvent.event_type == event_type)
        if direction:
            stmt = stmt.where(FinancialEvent.direction == direction)
        if amount_min is not None:
            stmt = stmt.where(FinancialEvent.amount >= amount_min)
        if amount_max is not None:
            stmt = stmt.where(FinancialEvent.amount <= amount_max)
        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    FinancialEvent.description.ilike(search_pattern),
                    FinancialEvent.raw_message.ilike(search_pattern),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_res = await self.session.execute(count_stmt)
        total = total_res.scalar_one_or_none() or 0

        stmt = stmt.options(
            selectinload(FinancialEvent.account),
            selectinload(FinancialEvent.category)
        )
        stmt = stmt.order_by(FinancialEvent.occurred_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_event_detail(self, user_id: UUID, event_id: UUID) -> FinancialEvent | None:
        from sqlalchemy.orm import selectinload
        stmt = select(FinancialEvent).where(
            FinancialEvent.id == event_id,
            FinancialEvent.user_id == user_id
        ).options(
            selectinload(FinancialEvent.account),
            selectinload(FinancialEvent.category)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_summary(
        self,
        user_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict:
        from sqlalchemy import func
        stmt = select(
            FinancialEvent.event_type,
            FinancialEvent.direction,
            func.sum(FinancialEvent.amount).label("total_amount"),
            func.count(FinancialEvent.id).label("count")
        ).where(FinancialEvent.user_id == user_id)

        if date_from:
            stmt = stmt.where(FinancialEvent.occurred_at >= date_from)
        if date_to:
            stmt = stmt.where(FinancialEvent.occurred_at <= date_to)

        stmt = stmt.group_by(FinancialEvent.event_type, FinancialEvent.direction)
        result = await self.session.execute(stmt)
        rows = result.all()

        summary = {
            "total_income": Decimal("0"),
            "total_expense": Decimal("0"),
            "total_goal_contributions": Decimal("0"),
            "total_obligation_payments": Decimal("0"),
            "total_credit_card_payments": Decimal("0"),
            "total_credit_card_purchases": Decimal("0"),
            "events_count": 0,
            "net_cashflow": Decimal("0")
        }

        for row in rows:
            etype = row.event_type
            direction = row.direction
            total_amt = row.total_amount or Decimal("0")
            count = row.count or 0
            
            summary["events_count"] += count

            if etype == "income":
                summary["total_income"] += total_amt
            elif etype == "expense":
                summary["total_expense"] += total_amt
            elif etype == "goal_contribution":
                summary["total_goal_contributions"] += total_amt
            elif etype == "obligation_payment":
                summary["total_obligation_payments"] += total_amt
            elif etype == "credit_card_payment":
                summary["total_credit_card_payments"] += total_amt
            elif etype == "credit_card_purchase":
                summary["total_credit_card_purchases"] += total_amt

            if etype not in ("transfer_in", "transfer_out"):
                if direction == "inflow":
                    summary["net_cashflow"] += total_amt
                elif direction == "outflow":
                    summary["net_cashflow"] -= total_amt

        return summary

    async def get_timeline(
        self,
        user_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[FinancialEvent]:
        # Return all events in period to group them in service
        from sqlalchemy.orm import selectinload
        stmt = select(FinancialEvent).where(FinancialEvent.user_id == user_id)
        if date_from:
            stmt = stmt.where(FinancialEvent.occurred_at >= date_from)
        if date_to:
            stmt = stmt.where(FinancialEvent.occurred_at <= date_to)
        
        stmt = stmt.options(
            selectinload(FinancialEvent.account),
            selectinload(FinancialEvent.category)
        )
        stmt = stmt.order_by(FinancialEvent.occurred_at.asc())
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
