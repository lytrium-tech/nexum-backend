"""
app/ledger/service.py
=====================
Servicio de dominio para Ledger.
Aplica lógicas de negocio puras (no-persistentes) como el cálculo del `period`.
Actúa de interfaz segura entre el dominio originador (Cash) y el repository.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.ledger.repository import LedgerRepository
from app.ledger.schemas import LedgerEventCreate, LedgerEventResult


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
        groups = defaultdict(lambda: {
            "income": Decimal("0"),
            "expense": Decimal("0"),
            "net": Decimal("0"),
            "items": []
        })

        for event in events:
            date_str = event.occurred_at.strftime("%Y-%m-%d")
            groups[date_str]["items"].append(event)
            if event.direction == "inflow":
                groups[date_str]["income"] += event.amount
                groups[date_str]["net"] += event.amount
            elif event.direction == "outflow":
                groups[date_str]["expense"] += event.amount
                groups[date_str]["net"] -= event.amount

        result = []
        for date_str, data in groups.items():
            result.append({
                "date": date_str,
                "income": data["income"],
                "expense": data["expense"],
                "net": data["net"],
                "items": data["items"]
            })
            
        result.sort(key=lambda x: x["date"], reverse=True)
        return result
