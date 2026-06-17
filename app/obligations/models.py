import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    payment_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="fixed_full_payment")
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    category_id: Mapped[UUID | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )


class ObligationPayment(Base):
    __tablename__ = "obligation_payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(ForeignKey("obligations.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("financial_events.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
