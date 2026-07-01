import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, CheckConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[UUID | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    payment_mode: Mapped[str] = mapped_column(Text, nullable=False)
    base_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    due_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")


class ObligationPeriod(Base):
    __tablename__ = "obligation_periods"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False)
    period_key: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("obligation_id", "period_key", name="uq_obligation_period_key"),
        CheckConstraint("paid_amount >= 0", name="chk_paid_amount_positive"),
        CheckConstraint("amount IS NULL OR paid_amount <= amount", name="chk_paid_amount_lte_amount"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="chk_amount_positive"),
    )


class ObligationPayment(Base):
    __tablename__ = "obligation_payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    obligation_id: Mapped[UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False)
    obligation_period_id: Mapped[UUID] = mapped_column(ForeignKey("obligation_periods.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    financial_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("financial_events.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    source_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    source_currency: Mapped[str] = mapped_column(Text, nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(15, 6), nullable=True)
    rate_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    is_estimated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
