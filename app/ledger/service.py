"""
app/ledger/service.py
=====================
Servicio de dominio para Ledger.
Aplica lógicas de negocio puras (no-persistentes) como el cálculo del `period`.
Actúa de interfaz segura entre el dominio originador (Cash) y el repository.
"""

from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InfrastructureError,
    NotFoundError,
    ValidationError,
)
from app.ledger.models import FinancialEventReclassification
from app.ledger.repository import LedgerRepository
from app.ledger.schemas import (
    LedgerEventCreate,
    LedgerEventResult,
    ReclassificationRequest,
    ReclassificationResponse,
)


def generate_period_for_bogota(dt: datetime | None = None) -> str:
    """
    Helper puro para generar el string YYYY-MM en la zona horaria America/Bogota.
    Si dt es None, usa utcnow().
    """
    if dt is None:
        dt = datetime.now(UTC)

    # Convertir a timezone de Bogotá
    bogota_tz = ZoneInfo("America/Bogota")
    dt_bogota = dt.astimezone(bogota_tz)

    return dt_bogota.strftime("%Y-%m")


class LedgerService:
    """
    Servicio encapsulado para escrituras en Ledger.
    """

    def __init__(self, repository: LedgerRepository) -> None:
        self.repository = repository

    async def record_event(self, event_data: LedgerEventCreate) -> LedgerEventResult:
        """
        Punto de entrada principal para registrar un evento financiero.

        Aplica defaults de backend:
        1. Si no viene period, lo genera basado en occurred_at o el current time.
        2. Delega al repository para persistencia e idempotencia.
        """
        # Period is handled by DB trigger, we don't set it here

        # La validación de ownership se delega al repositorio porque
        # requiere acceso a bases de datos (consultas cruzadas).
        return await self.repository.insert_event(event_data)

    async def list_events(
        self,
        user_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        account_id: UUID | None = None,
        category_id: UUID | None = None,
        event_type: str | None = None,
        direction: str | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        return await self.repository.list_events(
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            category_id=category_id,
            event_type=event_type,
            direction=direction,
            amount_min=amount_min,
            amount_max=amount_max,
            search=search,
            limit=limit,
            offset=offset,
        )

    async def get_event_detail(self, user_id: UUID, event_id: UUID):
        from app.core.errors import NotFoundError

        event = await self.repository.get_event_detail(user_id, event_id)
        if not event:
            raise NotFoundError(message="Evento no encontrado.")
        return event

    async def get_summary(
        self,
        user_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict:
        return await self.repository.get_summary(user_id, date_from, date_to)

    async def get_timeline(
        self,
        user_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[dict]:
        from collections import defaultdict
        from decimal import Decimal

        events = await self.repository.get_timeline(user_id, date_from, date_to)

        # Agrupar por día
        groups = defaultdict(
            lambda: {
                "income": Decimal("0"),
                "expense": Decimal("0"),
                "net": Decimal("0"),
                "items": [],
            }
        )

        for event in events:
            date_str = event.occurred_at.strftime("%Y-%m-%d")
            groups[date_str]["items"].append(event)
            if event.event_type not in ("transfer_in", "transfer_out"):
                if event.direction == "inflow":
                    groups[date_str]["income"] += event.amount
                    groups[date_str]["net"] += event.amount
                elif event.direction == "outflow":
                    groups[date_str]["expense"] += event.amount
                    groups[date_str]["net"] -= event.amount

        result = []
        for date_str, data in groups.items():
            result.append(
                {
                    "date": date_str,
                    "income": data["income"],
                    "expense": data["expense"],
                    "net": data["net"],
                    "items": data["items"],
                }
            )

        result.sort(key=lambda x: x["date"], reverse=True)
        return result

    def _is_idempotent_match(
        self,
        existing: FinancialEventReclassification,
        user_id: UUID,
        event_id: UUID,
        new_category_id: UUID,
        normalized_reason: str | None,
    ) -> bool:
        return (
            str(existing.reclassified_by) == str(user_id)
            and str(existing.financial_event_id) == str(event_id)
            and str(existing.new_category_id) == str(new_category_id)
            and existing.reason == normalized_reason
            and existing.source == "manual"
        )

    def _build_idempotent_response(
        self, existing: FinancialEventReclassification
    ) -> ReclassificationResponse:
        return ReclassificationResponse(
            status="idempotent_retry",
            reclassification_id=existing.id,
            event_id=existing.financial_event_id,
            previous_category_id=existing.previous_category_id,
            new_category_id=existing.new_category_id,
            idempotency_key=existing.command_id,
            source=existing.source,
            reason=existing.reason,
            created_at=existing.created_at,
        )

    async def reclassify_event(
        self, user_id: UUID, event_id: UUID, request: ReclassificationRequest
    ) -> ReclassificationResponse:
        """
        Reclasifica analticamente un evento financiero (solo cambia category_id).
        Aplica validaciones estrictas, idempotencia y locking.
        """
        from sqlalchemy.exc import IntegrityError

        # 1. Normalizar reason
        reason = request.reason.strip() if request.reason else None
        if reason == "":
            reason = None

        try:
            async with self.repository.session.begin_nested():
                # 2. Buscar y bloquear el evento (ownership check first)
                event = await self.repository.get_event_for_update(event_id, user_id)
                if not event:
                    raise NotFoundError(message="Event not found", error_code="event_not_found")

                # 3. Consultar command_id despus del lock
                existing_reclass = await self.repository.get_reclassification_by_command_id(
                    request.idempotency_key
                )
                if existing_reclass:
                    if self._is_idempotent_match(
                        existing_reclass, user_id, event_id, request.new_category_id, reason
                    ):
                        return self._build_idempotent_response(existing_reclass)
                    else:
                        raise ConflictError(
                            message="Idempotency key reused for a different operation",
                            error_code="idempotency_key_reused",
                        )

                # 4. Validar allowlist
                if event.event_type not in ("income", "expense"):
                    raise ValidationError(
                        message="Event type not reclassifiable",
                        error_code="event_not_reclassifiable",
                    )

                # 5. Validar que category_id sea distinto
                if str(event.category_id) == str(request.new_category_id):
                    raise ConflictError(
                        message="Category already assigned", error_code="category_already_assigned"
                    )

                # 6. Bloquear y validar categora destino
                new_category = await self.repository.get_category_for_update(
                    request.new_category_id
                )
                if not new_category:
                    raise NotFoundError(
                        message="Category not found", error_code="category_not_found"
                    )

                if new_category.user_id is not None and str(new_category.user_id) != str(user_id):
                    raise ForbiddenError(
                        message="Category belongs to another user", error_code="category_forbidden"
                    )

                if not new_category.is_active:
                    raise ValidationError(
                        message="Category is inactive", error_code="category_inactive"
                    )

                if new_category.type != event.event_type:
                    raise ValidationError(
                        message=f"Category type mismatch. Expected {event.event_type}",
                        error_code="category_type_mismatch",
                    )

                # 7. Capturar estado previo
                previous_category_id = event.category_id

                # 8. Crear auditora
                reclass = FinancialEventReclassification(
                    financial_event_id=event.id,
                    previous_category_id=previous_category_id,
                    new_category_id=new_category.id,
                    reclassified_by=user_id,
                    source="manual",
                    reason=reason,
                    command_id=request.idempotency_key,
                )
                self.repository.create_reclassification(reclass)

                # 9. Modificar evento
                event.category_id = new_category.id

                # 10. Flush para materializar auditora
                await self.repository.session.flush()

        except IntegrityError as exc:
            # begin_nested hace rollback automticamente de las operaciones de escritura
            constraint_name = getattr(exc.orig, "constraint_name", None)
            error_msg = str(exc.orig)

            is_idempotency = (
                constraint_name
                in (
                    "uq_financial_event_reclassifications_command_id",
                    "idx_financial_events_command_id",
                )
                or "uq_financial_event_reclassifications_command_id" in error_msg
                or "idx_financial_events_command_id" in error_msg
            )

            if is_idempotency:
                # Recuperar tras IntegrityError
                existing_reclass_concurrent = (
                    await self.repository.get_reclassification_by_command_id(
                        request.idempotency_key
                    )
                )
                if not existing_reclass_concurrent:
                    raise InfrastructureError("Error recovering idempotent reclassification")

                if self._is_idempotent_match(
                    existing_reclass_concurrent, user_id, event_id, request.new_category_id, reason
                ):
                    return self._build_idempotent_response(existing_reclass_concurrent)
                else:
                    raise ConflictError(
                        message="Idempotency key reused for a different operation",
                        error_code="idempotency_key_reused",
                    )
            else:
                raise

        # Transacción exitosa
        return ReclassificationResponse(
            status="reclassified",
            reclassification_id=reclass.id,
            event_id=event.id,
            previous_category_id=previous_category_id,
            new_category_id=new_category.id,
            idempotency_key=reclass.command_id,
            source=reclass.source,
            reason=reclass.reason,
            created_at=reclass.created_at,
        )
