import uuid
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.goals.enums import GoalTransactionType


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
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="COP")

    applied_amount: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    goal_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    rate_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

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


class GoalTransaction(Base):
    __tablename__ = "goal_transactions"

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    goal_id: Mapped[UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("financial_events.id", ondelete="RESTRICT"), nullable=True
    )
    transaction_type: Mapped[GoalTransactionType] = mapped_column(
        Enum(GoalTransactionType, name="goal_transaction_type"), nullable=False
    )
    source_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    applied_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    goal_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    command_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True), nullable=True)
    command_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_contribution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("goal_contributions.id", ondelete="RESTRICT"), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(astext_type=Text()), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("source_amount > 0", name="chk_gtx_source_pos"),
        CheckConstraint("applied_amount > 0", name="chk_gtx_applied_pos"),
        CheckConstraint(
            "(transaction_type = 'legacy_import') OR (event_id IS NOT NULL)",
            name="chk_gtx_event_id",
        ),
        CheckConstraint(
            "(command_id IS NULL AND command_fingerprint IS NULL) OR "
            "(command_id IS NOT NULL AND command_fingerprint IS NOT NULL)",
            name="chk_gtx_fingerprint",
        ),
        UniqueConstraint("command_id", name="uq_gtx_command_id"),
        UniqueConstraint("legacy_contribution_id", name="uq_gtx_legacy_id"),
        Index("ix_goal_transactions_goal_id", "goal_id"),
        Index("ix_goal_transactions_account_id", "account_id"),
        Index("ix_goal_transactions_user_id", "user_id"),
        Index("ix_goal_transactions_created_at", "created_at"),
        Index("ix_goal_transactions_tx_type", "transaction_type"),
    )
