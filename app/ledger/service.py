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
        if not event_data.period:
            event_data.period = generate_period_for_bogota(event_data.occurred_at)

        # La validación de ownership se delega al repositorio porque
        # requiere acceso a bases de datos (consultas cruzadas).
        return await self.repository.insert_event(event_data)
