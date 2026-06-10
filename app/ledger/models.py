"""
app/ledger/models.py
====================
Mapeo SQLAlchemy de la tabla public.financial_events.

NOTA: No se ejecuta metadata.create_all() ni se modifican migraciones.
La tabla ya existe en Supabase y este modelo debe coincidir estructuralmente.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKey,
    Index,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class FinancialEvent(Base):
    """
    Fuente histórica de verdad para movimientos financieros.
    """

    __tablename__ = "financial_events"

    # ── Columnas ──────────────────────────────────────────────────────────────
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    category_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="backend")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    period: Mapped[str] = mapped_column(Text, nullable=False, server_default=FetchedValue())
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    command_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    # ── Constraints e Índices de lectura (reflejando la DB real) ──────────────
    __table_args__ = (
        CheckConstraint("amount > 0", name="financial_events_amount_check"),
        CheckConstraint(
            "direction = ANY (ARRAY['inflow', 'outflow', 'neutral'])",
            name="financial_events_direction_check",
        ),
        CheckConstraint(
            "event_type = ANY (ARRAY['income', 'expense', 'credit_card_purchase', "
            "'credit_card_payment', 'obligation_payment', "
            "'goal_contribution', 'manual_adjustment'])",
            name="financial_events_type_check",
        ),
        # Índice único parcial que garantiza idempotencia
        Index(
            "idx_financial_events_command_id",
            "command_id",
            unique=True,
            postgresql_where="command_id IS NOT NULL",
        ),
    )
