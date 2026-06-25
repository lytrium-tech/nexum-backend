import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CreditCard(Base):
    __tablename__ = "credit_cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bank: Mapped[str] = mapped_column(String, nullable=False)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    current_debt: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    cutoff_day: Mapped[int] = mapped_column(Integer, nullable=False)
    due_day: Mapped[int] = mapped_column(Integer, nullable=False)
    management_fee: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    monthly_interest_rate: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    annual_interest_rate: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    network: Mapped[str | None] = mapped_column(String, nullable=True)
    franchise: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="COP")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardTransaction(Base):
    __tablename__ = "credit_card_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    installments_total: Mapped[int] = mapped_column(Integer, default=1)
    installments_paid: Mapped[int] = mapped_column(Integer, default=0)
    monthly_amount: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    interest_amount: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    total_with_interest: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    period: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, default="backend")
    assigned_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_card_statements.id"), nullable=True
    )
    statement_assignment_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardInstallment(Base):
    __tablename__ = "credit_card_installments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id"), nullable=False
    )
    purchase_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_card_transactions.id"), nullable=False
    )
    installment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    installments_total: Mapped[int] = mapped_column(Integer, nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    scheduled_period: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    paid_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0.00"))
    interest_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0.00"))
    scheduled_due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    revision_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardStatement(Base):
    __tablename__ = "credit_card_statements"
    __table_args__ = (
        UniqueConstraint("credit_card_id", "billing_period", name="uq_cc_statement_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id"), nullable=False
    )
    billing_period: Mapped[str] = mapped_column(String, nullable=False)
    billing_period_start: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    cutoff_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    previous_balance: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    new_purchases: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    billed_installments: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    fees_total: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    interest_total: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    payments_received: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    statement_balance: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    minimum_payment: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardEarlyPayment(Base):
    __tablename__ = "credit_card_early_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id"), nullable=False
    )
    purchase_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_card_transactions.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    allocation_mode: Mapped[str] = mapped_column(String, nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardStatementCharge(Base):
    __tablename__ = "credit_card_statement_charges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_card_statements.id"), nullable=False
    )
    charge_type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    paid_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

