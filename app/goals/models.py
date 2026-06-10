import uuid
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GoalContribution(Base):
    __tablename__ = "goal_contributions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    goal_id: Mapped[UUID] = mapped_column(ForeignKey("goals.id"), nullable=False)
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("financial_events.id"), nullable=True)
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    period: Mapped[str | None] = mapped_column(Text, nullable=True)
    contributed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
