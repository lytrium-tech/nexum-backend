import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.credit.exceptions import CreditStatementImmutableError

# ── Partition & Provenance Constants ─────────────────────────────────────────

DEBT_PARTITION_LEGACY = "legacy"
DEBT_PARTITION_CANONICAL = "canonical"
ALLOWED_DEBT_PARTITIONS = (DEBT_PARTITION_LEGACY, DEBT_PARTITION_CANONICAL)

PROVENANCE_LEGACY_UNKNOWN = "legacy_unknown"
PROVENANCE_NEXUM_MODELED = "nexum_modeled"
PROVENANCE_ISSUER_AUTHORITATIVE = "issuer_authoritative"
PROVENANCE_MANUAL = "manual"

ALLOCATION_SOURCE_NEXUM_MODELED = "nexum_modeled"
ALLOCATION_SOURCE_ISSUER_AUTHORITATIVE = "issuer_authoritative"
ALLOCATION_SOURCE_USER_DIRECTED = "user_directed"


class CreditCard(Base):
    __tablename__ = "credit_cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bank: Mapped[str] = mapped_column(String, nullable=False)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    # Retained physically for backwards compatibility; non-authoritative legacy field.
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

    # Canonical activation, provenance and lifecycle
    canonical_mode: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    canonical_activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provenance: Mapped[str] = mapped_column(
        String, default=PROVENANCE_LEGACY_UNKNOWN, server_default=text("'legacy_unknown'::text")
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String, default="active", server_default=text("'active'::text")
    )

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
    zero_interest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
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

    # Explicit multi-currency, cycle identity, provenance and debt partition
    original_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    original_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    booked_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    booked_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(precision=18, scale=8), nullable=True)
    fx_rate_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_rates.id", ondelete="SET NULL"), nullable=True
    )
    fx_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fx_quotes.id", ondelete="SET NULL"), nullable=True
    )
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_billing_cycles.id", ondelete="SET NULL"),
        nullable=True,
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String, default="posted", server_default=text("'posted'::text")
    )
    provenance: Mapped[str] = mapped_column(
        String, default=PROVENANCE_LEGACY_UNKNOWN, server_default=text("'legacy_unknown'::text")
    )
    debt_partition: Mapped[str] = mapped_column(
        String, default=DEBT_PARTITION_LEGACY, server_default=text("'legacy'::text")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardInstallment(Base):
    __tablename__ = "credit_card_installments"
    __table_args__ = (
        Index(
            "ix_cc_installments_card_due_active",
            "credit_card_id",
            "scheduled_due_date",
            postgresql_where=text("status <> 'paid' AND lifecycle_status = 'active'"),
        ),
    )

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
    interest_amount: Mapped[Decimal] = mapped_column(
        Numeric, nullable=False, default=Decimal("0.00")
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("0.00"))
    scheduled_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    revision_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Explicit multi-currency, cycle identity, assessed vs estimated interest, provenance and partition
    original_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    original_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    booked_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    booked_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_billing_cycles.id", ondelete="SET NULL"),
        nullable=True,
    )
    assessed_interest_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    opening_balance_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    estimated_interest_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    interest_estimator_version: Mapped[str | None] = mapped_column(String, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(
        String, default="active", server_default=text("'active'::text")
    )
    provenance: Mapped[str] = mapped_column(
        String, default=PROVENANCE_LEGACY_UNKNOWN, server_default=text("'legacy_unknown'::text")
    )
    debt_partition: Mapped[str] = mapped_column(
        String, default=DEBT_PARTITION_LEGACY, server_default=text("'legacy'::text")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditCardStatement(Base):
    __tablename__ = "credit_card_statements"
    __table_args__ = (
        UniqueConstraint("credit_card_id", "billing_period", name="uq_cc_statement_period"),
        Index(
            "uq_cc_statements_canonical_cycle_id",
            "cycle_id",
            unique=True,
            postgresql_where=text("cycle_id IS NOT NULL AND debt_partition = 'canonical'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id"), nullable=False
    )
    billing_period: Mapped[str] = mapped_column(String, nullable=False)
    billing_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    cutoff_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    previous_balance: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    new_purchases: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    billed_installments: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    fees_total: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    interest_total: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    payments_received: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    statement_balance: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.00"))
    minimum_payment: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Canonical cycle identity, currency, assessed interest and partition
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_billing_cycles.id", ondelete="SET NULL"),
        nullable=True,
    )
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    assessed_interest: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    estimated_interest: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    provenance: Mapped[str] = mapped_column(
        String, default=PROVENANCE_LEGACY_UNKNOWN, server_default=text("'legacy_unknown'::text")
    )
    debt_partition: Mapped[str] = mapped_column(
        String, default=DEBT_PARTITION_LEGACY, server_default=text("'legacy'::text")
    )

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
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    # Link to transactions, explicit currency/amounts, provenance and partition
    payment_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    original_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    booked_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    booked_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    provenance: Mapped[str] = mapped_column(
        String, default=PROVENANCE_LEGACY_UNKNOWN, server_default=text("'legacy_unknown'::text")
    )
    debt_partition: Mapped[str] = mapped_column(
        String, default=DEBT_PARTITION_LEGACY, server_default=text("'legacy'::text")
    )

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


# ── Canonical Domain Models (Goal 2 Foundation) ──────────────────────────────


class CreditCardBillingCycle(Base):
    __tablename__ = "credit_card_billing_cycles"
    __table_args__ = (
        UniqueConstraint(
            "credit_card_id", "billing_period", name="uq_cc_billing_cycles_card_period"
        ),
        CheckConstraint("start_date <= cutoff_date", name="chk_cc_billing_cycles_start_cutoff"),
        CheckConstraint("cutoff_date <= due_date", name="chk_cc_billing_cycles_cutoff_due"),
        CheckConstraint(
            "status = ANY (ARRAY['open'::text, 'closed'::text, 'settled'::text])",
            name="chk_cc_billing_cycles_status",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_billing_cycles_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    billing_period: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="open", server_default=text("'open'::text"), nullable=False
    )
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CreditCardStatementComponent(Base):
    __tablename__ = "credit_card_statement_components"
    __table_args__ = (
        CheckConstraint("amount >= 0::numeric", name="chk_cc_stmt_comp_amount_nonneg"),
        CheckConstraint(
            "principal_amount >= 0::numeric AND interest_amount >= 0::numeric AND fee_amount >= 0::numeric",
            name="chk_cc_stmt_comp_parts_nonneg",
        ),
        CheckConstraint(
            "amount = (principal_amount + interest_amount + fee_amount)",
            name="chk_cc_stmt_comp_conservation",
        ),
        CheckConstraint(
            "component_type = ANY (ARRAY['purchase'::text, 'installment'::text, 'fee'::text, 'interest'::text, 'refund'::text, 'reversal'::text, 'adjustment'::text, 'payment'::text, 'prior_balance'::text, 'prior_credit'::text])",
            name="chk_cc_stmt_comp_type",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_stmt_comp_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_statements.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=14, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String, default="COP", server_default=text("'COP'::text"), nullable=False
    )
    principal_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    interest_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    installment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_installments.id", ondelete="SET NULL"),
        nullable=True,
    )
    statement_charge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_statement_charges.id", ondelete="SET NULL"),
        nullable=True,
    )
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreditCardStatementFact(Base):
    __tablename__ = "credit_card_statement_facts"
    __table_args__ = (
        CheckConstraint(
            "amount IS NULL OR amount >= 0::numeric",
            name="chk_cc_stmt_facts_amount_nonneg",
        ),
        CheckConstraint(
            "fact_type = ANY (ARRAY['minimum_payment_due'::text, 'statement_balance'::text, 'interest_rate'::text, 'fee_assessed'::text])",
            name="chk_cc_stmt_facts_type",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['issuer_authoritative'::text, 'nexum_modeled'::text, 'manual'::text])",
            name="chk_cc_stmt_facts_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_statements.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_type: Mapped[str] = mapped_column(String, nullable=False)
    # May be NULL when fact is absent/unknown; never fabricate 0.00
    amount: Mapped[Decimal | None] = mapped_column(Numeric(precision=14, scale=2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_ISSUER_AUTHORITATIVE,
        server_default=text("'issuer_authoritative'::text"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Append-only: created_at without updated_at
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreditCardPayment(Base):
    __tablename__ = "credit_card_payments"
    __table_args__ = (
        CheckConstraint("booked_amount > 0::numeric", name="chk_cc_payments_amount_pos"),
        CheckConstraint(
            "source_amount IS NULL OR source_amount > 0::numeric",
            name="chk_cc_payments_source_amount_pos",
        ),
        CheckConstraint(
            "principal_applied >= 0::numeric AND interest_applied >= 0::numeric AND fee_applied >= 0::numeric AND unallocated_amount >= 0::numeric",
            name="chk_cc_payments_applied_nonneg",
        ),
        CheckConstraint(
            "booked_amount = (principal_applied + interest_applied + fee_applied + unallocated_amount)",
            name="chk_cc_payments_conservation",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'posted'::text, 'reversed'::text])",
            name="chk_cc_payments_status",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_payments_provenance",
        ),
        CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_payments_alloc_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    payment_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_events.id", ondelete="SET NULL"), nullable=True
    )
    # Explicit booked amount & currency in card currency
    booked_amount: Mapped[Decimal] = mapped_column(Numeric(precision=14, scale=2), nullable=False)
    booked_currency: Mapped[str] = mapped_column(
        String, default="COP", server_default=text("'COP'::text"), nullable=False
    )
    source_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    source_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(precision=18, scale=8), nullable=True)
    fx_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fx_quotes.id", ondelete="SET NULL"), nullable=True
    )
    fx_rate_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_rates.id", ondelete="SET NULL"), nullable=True
    )
    principal_applied: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    interest_applied: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    fee_applied: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    unallocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="posted", server_default=text("'posted'::text"), nullable=False
    )
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    allocation_source: Mapped[str] = mapped_column(
        String,
        default=ALLOCATION_SOURCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    # command_id matches financial_events.command_id as UUID
    command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    command_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def amount(self) -> Decimal:
        """Compatibility alias for booked_amount in card currency."""
        return self.booked_amount

    @amount.setter
    def amount(self, value: Decimal) -> None:
        self.booked_amount = value

    @property
    def currency(self) -> str:
        """Compatibility alias for booked_currency."""
        return self.booked_currency

    @currency.setter
    def currency(self, value: str) -> None:
        self.booked_currency = value


class CreditCardPaymentAllocation(Base):
    __tablename__ = "credit_card_payment_allocations"
    __table_args__ = (
        CheckConstraint(
            "((CASE WHEN target_installment_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_charge_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_statement_component_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="chk_cc_alloc_exactly_one_target",
        ),
        CheckConstraint("allocated_amount > 0::numeric", name="chk_cc_alloc_amount_pos"),
        CheckConstraint(
            "allocated_principal >= 0::numeric AND allocated_interest >= 0::numeric AND allocated_fees >= 0::numeric",
            name="chk_cc_alloc_parts_nonneg",
        ),
        CheckConstraint(
            "allocated_amount = (allocated_principal + allocated_interest + allocated_fees)",
            name="chk_cc_alloc_conservation",
        ),
        CheckConstraint("allocation_order >= 1", name="chk_cc_alloc_order_pos"),
        CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_alloc_source",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_alloc_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_installment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_installments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_charge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_statement_charges.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_statement_component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_statement_components.id", ondelete="RESTRICT"),
        nullable=True,
    )
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), nullable=False
    )
    allocated_principal: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    allocated_interest: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    allocated_fees: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    allocation_order: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    allocation_source: Mapped[str] = mapped_column(
        String,
        default=ALLOCATION_SOURCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreditCardAdjustment(Base):
    __tablename__ = "credit_card_adjustments"
    __table_args__ = (
        CheckConstraint("booked_amount > 0::numeric", name="chk_cc_adjustments_amount_pos"),
        CheckConstraint(
            "original_amount IS NULL OR original_amount > 0::numeric",
            name="chk_cc_adjustments_orig_amount_pos",
        ),
        CheckConstraint(
            "adjustment_type = ANY (ARRAY['refund'::text, 'reversal'::text, 'chargeback'::text, 'statement_credit'::text, 'manual_adjustment'::text])",
            name="chk_cc_adjustments_type",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'posted'::text, 'reversed'::text])",
            name="chk_cc_adjustments_status",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_adjustments_provenance",
        ),
        CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_adjustments_alloc_source",
        ),
        CheckConstraint(
            "direction IS NULL OR direction = ANY (ARRAY['debit'::text, 'credit'::text])",
            name="chk_cc_adjustments_direction",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    original_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    reversal_of_adjustment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_adjustments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("financial_events.id", ondelete="SET NULL"), nullable=True
    )
    adjustment_type: Mapped[str] = mapped_column(String, nullable=False)
    # Explicit additive direction for canonical manual/statement adjustments ('debit' or 'credit')
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    # Explicit booked amount and currency
    booked_amount: Mapped[Decimal] = mapped_column(Numeric(precision=14, scale=2), nullable=False)
    booked_currency: Mapped[str] = mapped_column(
        String, default="COP", server_default=text("'COP'::text"), nullable=False
    )
    original_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    original_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(precision=18, scale=8), nullable=True)
    fx_rate_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_rates.id", ondelete="SET NULL"), nullable=True
    )
    fx_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fx_quotes.id", ondelete="SET NULL"), nullable=True
    )
    fx_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    fx_rate_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String, default="posted", server_default=text("'posted'::text"), nullable=False
    )
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    allocation_source: Mapped[str] = mapped_column(
        String,
        default=ALLOCATION_SOURCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    # command_id matches financial_events.command_id as UUID
    command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    command_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def amount(self) -> Decimal:
        """Compatibility alias for booked_amount in card currency."""
        return self.booked_amount

    @amount.setter
    def amount(self, value: Decimal) -> None:
        self.booked_amount = value

    @property
    def currency(self) -> str:
        """Compatibility alias for booked_currency."""
        return self.booked_currency

    @currency.setter
    def currency(self, value: str) -> None:
        self.booked_currency = value


class CreditCardAdjustmentAllocation(Base):
    __tablename__ = "credit_card_adjustment_allocations"
    __table_args__ = (
        CheckConstraint(
            "((CASE WHEN target_installment_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_transaction_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_charge_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="chk_cc_adj_alloc_exactly_one_target",
        ),
        CheckConstraint("allocated_amount > 0::numeric", name="chk_cc_adj_alloc_amount_pos"),
        CheckConstraint(
            "allocated_principal >= 0::numeric AND allocated_interest >= 0::numeric AND allocated_fees >= 0::numeric",
            name="chk_cc_adj_alloc_parts_nonneg",
        ),
        CheckConstraint(
            "allocated_amount = (allocated_principal + allocated_interest + allocated_fees)",
            name="chk_cc_adj_alloc_conservation",
        ),
        CheckConstraint("allocation_order >= 1", name="chk_cc_adj_alloc_order_pos"),
        CheckConstraint(
            "allocation_source = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'user_directed'::text])",
            name="chk_cc_adj_alloc_source",
        ),
        CheckConstraint(
            "provenance = ANY (ARRAY['nexum_modeled'::text, 'issuer_authoritative'::text, 'manual'::text, 'legacy_unknown'::text])",
            name="chk_cc_adj_alloc_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    credit_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_cards.id", ondelete="CASCADE"), nullable=False
    )
    adjustment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_adjustments.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_installment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_installments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_charge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_statement_charges.id", ondelete="RESTRICT"),
        nullable=True,
    )
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), nullable=False
    )
    allocated_principal: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    allocated_interest: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    allocated_fees: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=2), default=Decimal("0.00"), server_default=text("0.00")
    )
    allocation_order: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    allocation_source: Mapped[str] = mapped_column(
        String,
        default=ALLOCATION_SOURCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    provenance: Mapped[str] = mapped_column(
        String,
        default=PROVENANCE_NEXUM_MODELED,
        server_default=text("'nexum_modeled'::text"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── Closed Statement and Component Immutability Listeners ────────────────────

_IMMUTABLE_STATEMENT_STATUSES = frozenset({"closed", "settled"})


@event.listens_for(CreditCardStatement, "before_update")
def _statement_before_update(mapper, connection, target: CreditCardStatement) -> None:
    state = inspect(target)
    history = state.get_history("status", False)
    committed_status = state.committed_state.get("status")
    was_immutable = bool(
        committed_status in _IMMUTABLE_STATEMENT_STATUSES
        or (
            history.has_changes()
            and history.deleted
            and history.deleted[0] in _IMMUTABLE_STATEMENT_STATUSES
        )
    )
    is_transition_from_mutable = bool(
        history.has_changes()
        and history.deleted
        and history.deleted[0] not in _IMMUTABLE_STATEMENT_STATUSES
    )
    if is_transition_from_mutable:
        return

    if was_immutable or target.status in _IMMUTABLE_STATEMENT_STATUSES:
        raise CreditStatementImmutableError(
            message=f"Credit card statement is immutable in status '{target.status}'; no in-place updates allowed",
            detail={"statement_id": str(target.id), "status": target.status},
        )


@event.listens_for(CreditCardStatement, "before_delete")
def _statement_before_delete(mapper, connection, target: CreditCardStatement) -> None:
    if target.status in _IMMUTABLE_STATEMENT_STATUSES:
        raise CreditStatementImmutableError(
            message=f"Credit card statement snapshot in status '{target.status}' cannot be deleted",
            detail={"statement_id": str(target.id), "status": target.status},
        )


@event.listens_for(CreditCardStatementComponent, "before_update")
def _component_before_update(mapper, connection, target: CreditCardStatementComponent) -> None:
    raise CreditStatementImmutableError(
        message="Statement components are immutable and cannot be updated in place",
        detail={"component_id": str(target.id)},
    )


@event.listens_for(CreditCardStatementComponent, "before_delete")
def _component_before_delete(mapper, connection, target: CreditCardStatementComponent) -> None:
    raise CreditStatementImmutableError(
        message="Statement components are immutable and cannot be deleted",
        detail={"component_id": str(target.id)},
    )
