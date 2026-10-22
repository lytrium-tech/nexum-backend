"""
app/obligations/settlement.py
=============================
Shared settlement, FX conversion, and idempotency helpers for Obligations.
Consolidates source amount authority, quote/snapshot validation,
FIFO allocation reconciliation, and deterministic request fingerprinting.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.core.currency import round_to_minimum_unit
from app.obligations.enums_v17 import FXQuoteStatus
from app.obligations.models import (
    ExchangeRate,
    FXQuote,
    Obligation,
    ObligationPayment,
    ObligationPeriod,
)

_MAX_PAYMENT_AMOUNT = Decimal("9999999999999.99")
_PAYMENT_QUANTUM = Decimal("0.01")


@dataclass
class SettlementResult:
    source_currency: str
    source_amount: Decimal
    applied_amount: Decimal
    fx_rate: Decimal
    rate_source: str | None
    rate_timestamp: datetime | None
    quote_id: UUID | None
    rate_snapshot_id: UUID | None


async def resolve_settlement(
    session: AsyncSession,
    user_id: UUID,
    obligation: Obligation,
    account: Account,
    requested_amount: Decimal,
    requested_currency: str | None = None,
    requested_source_currency: str | None = None,
    requested_source_amount: Decimal | None = None,
    quote_id: UUID | None = None,
    rate_snapshot_id: UUID | None = None,
) -> SettlementResult:
    """
    Authoritatively calculates applied and source amounts for an obligation payment.
    Enforces currency authority from Account, snapshot/quote validity and tolerances.
    Legacy user-supplied source_amount is NEVER authoritative.
    """
    if (
        requested_amount is None
        or not requested_amount.is_finite()
        or requested_amount <= Decimal("0")
        or requested_amount > _MAX_PAYMENT_AMOUNT
    ):
        raise HTTPException(status_code=422, detail="invalid_amount")
    if requested_amount.quantize(_PAYMENT_QUANTUM) != requested_amount:
        raise HTTPException(status_code=422, detail="invalid_amount_precision")

    obligation_currency = obligation.currency.upper()
    source_currency = account.currency.upper()

    if requested_currency and requested_currency.upper() != obligation_currency:
        raise HTTPException(status_code=422, detail="currency_mismatch")

    if requested_source_currency and requested_source_currency.upper() != source_currency:
        raise HTTPException(status_code=422, detail="currency_mismatch")

    # 1. Same-currency settlement: Authoritative debit EQUALS applied_amount
    if source_currency == obligation_currency:
        applied_amount = requested_amount
        source_amount = applied_amount
        if source_amount <= Decimal("0"):
            raise HTTPException(status_code=422, detail="invalid_amount")

        return SettlementResult(
            source_currency=source_currency,
            source_amount=source_amount,
            applied_amount=applied_amount,
            fx_rate=Decimal("1.00000000"),
            rate_source=None,
            rate_timestamp=None,
            quote_id=None,
            rate_snapshot_id=None,
        )

    # 2. Cross-currency settlement: Requires snapshot or quote
    if not rate_snapshot_id and not quote_id:
        raise HTTPException(status_code=422, detail="fx_rate_snapshot_required")

    now_utc = datetime.now(UTC)

    if rate_snapshot_id:
        stmt = select(ExchangeRate).where(ExchangeRate.id == rate_snapshot_id)
        snapshot = (await session.execute(stmt)).scalars().first()
        if not snapshot:
            raise HTTPException(status_code=404, detail="invalid_fx_rate_snapshot")

        snapshot_expires = snapshot.expires_at
        if snapshot_expires.tzinfo is None:
            snapshot_expires = snapshot_expires.replace(tzinfo=UTC)

        if snapshot_expires <= now_utc:
            raise HTTPException(status_code=422, detail="fx_rate_snapshot_expired")

        if (
            snapshot.base_currency != source_currency
            or snapshot.quote_currency != obligation_currency
        ):
            raise HTTPException(status_code=422, detail="invalid_fx_rate_snapshot")

        if not snapshot.rate or not snapshot.rate.is_finite() or snapshot.rate <= Decimal("0"):
            raise HTTPException(status_code=422, detail="invalid_fx_rate_snapshot")

        applied_amount = requested_amount
        fx_rate = snapshot.rate
        source_amount = round_to_minimum_unit(applied_amount / fx_rate, source_currency)
        if source_amount <= Decimal("0"):
            raise HTTPException(status_code=422, detail="below_minimum_amount")
        if source_amount > _MAX_PAYMENT_AMOUNT:
            raise HTTPException(status_code=422, detail="invalid_amount")

        rate_source = snapshot.provider
        rate_timestamp = snapshot.fetched_at.replace(tzinfo=None) if snapshot.fetched_at else None

        return SettlementResult(
            source_currency=source_currency,
            source_amount=source_amount,
            applied_amount=applied_amount,
            fx_rate=fx_rate,
            rate_source=rate_source,
            rate_timestamp=rate_timestamp,
            quote_id=None,
            rate_snapshot_id=snapshot.id,
        )

    # Cross-currency via FXQuote
    stmt = select(FXQuote).where(FXQuote.id == quote_id)
    quote = (await session.execute(stmt)).scalars().first()
    if not quote or quote.user_id != user_id:
        raise HTTPException(status_code=404, detail="fx_quote_not_found")

    quote_expires = quote.expires_at
    if quote_expires.tzinfo is None:
        quote_expires = quote_expires.replace(tzinfo=UTC)

    if quote_expires <= now_utc:
        raise HTTPException(status_code=422, detail="fx_quote_expired")

    if quote.status != FXQuoteStatus.active.value:
        raise HTTPException(status_code=422, detail="fx_quote_expired")

    if quote.from_currency != source_currency or quote.to_currency != obligation_currency:
        raise HTTPException(status_code=422, detail="invalid_fx_quote")

    if (
        not quote.rate
        or not quote.rate.is_finite()
        or quote.rate <= Decimal("0")
        or not quote.source_amount
        or not quote.source_amount.is_finite()
        or quote.source_amount <= Decimal("0")
        or not quote.target_amount
        or not quote.target_amount.is_finite()
        or quote.target_amount <= Decimal("0")
    ):
        raise HTTPException(status_code=422, detail="invalid_fx_quote")

    if (
        quote.source_amount > _MAX_PAYMENT_AMOUNT
        or quote.target_amount > _MAX_PAYMENT_AMOUNT
        or quote.source_amount.quantize(_PAYMENT_QUANTUM) != quote.source_amount
        or quote.target_amount.quantize(_PAYMENT_QUANTUM) != quote.target_amount
    ):
        raise HTTPException(status_code=422, detail="invalid_fx_quote")

    if requested_source_amount is not None and quote.source_amount != requested_source_amount:
        raise HTTPException(status_code=422, detail="invalid_fx_quote")

    # Source-based and target-based quotes can round on opposite sides of the pair.
    expected_target = round_to_minimum_unit(quote.source_amount * quote.rate, obligation_currency)
    expected_source = round_to_minimum_unit(quote.target_amount / quote.rate, source_currency)
    if quote.target_amount != expected_target and quote.source_amount != expected_source:
        raise HTTPException(status_code=422, detail="invalid_fx_quote")

    # Retain safe quote tolerance: 0.1% or minimum 50 COP / 0.01 other
    allowed_diff = max(
        requested_amount * Decimal("0.001"),
        Decimal("50.00") if obligation_currency == "COP" else Decimal("0.01"),
    )
    if abs(quote.target_amount - requested_amount) > allowed_diff:
        raise HTTPException(status_code=422, detail="invalid_fx_quote")

    applied_amount = quote.target_amount
    source_amount = quote.source_amount
    if source_amount <= Decimal("0"):
        raise HTTPException(status_code=422, detail="below_minimum_amount")

    fx_rate = quote.rate
    rate_source = quote.provider
    rate_timestamp = quote.rate_timestamp.replace(tzinfo=None) if quote.rate_timestamp else None

    return SettlementResult(
        source_currency=source_currency,
        source_amount=source_amount,
        applied_amount=applied_amount,
        fx_rate=fx_rate,
        rate_source=rate_source,
        rate_timestamp=rate_timestamp,
        quote_id=quote.id,
        rate_snapshot_id=None,
    )


def reconcile_fifo_source_allocations(
    total_source_amount: Decimal,
    allocations: list[tuple[ObligationPeriod, Decimal, Decimal]],
) -> list[Decimal]:
    """Allocate the authoritative debit proportionally; the final row absorbs cents."""
    if not allocations:
        return []
    total_applied = sum((amount for _, _, amount in allocations), Decimal("0"))
    if total_applied <= 0 or total_source_amount <= 0:
        raise HTTPException(status_code=422, detail="cannot_support_positive_slices")
    slices = []
    remaining = total_source_amount
    for index, (_, _, amount) in enumerate(allocations):
        source = (
            remaining
            if index == len(allocations) - 1
            else (total_source_amount * amount / total_applied).quantize(
                _PAYMENT_QUANTUM, rounding="ROUND_HALF_UP"
            )
        )
        if source <= 0:
            raise HTTPException(status_code=422, detail="cannot_support_positive_slices")
        slices.append(source)
        remaining -= source
    return slices


def build_idempotency_fingerprint(
    obligation_id: UUID,
    period_id: UUID | None,
    strategy: str,
    source_account_id: UUID,
    amount: Decimal,
    currency: str,
    source_currency: str | None = None,
    quote_id: UUID | None = None,
    rate_snapshot_id: UUID | None = None,
    payment_date: date | None = None,
) -> dict:
    return {
        "obligation_id": str(obligation_id),
        "period_id": str(period_id) if period_id else None,
        "strategy": strategy,
        "source_account_id": str(source_account_id),
        "amount": str(amount),
        "currency": currency.upper(),
        "source_currency": source_currency.upper() if source_currency else None,
        "quote_id": str(quote_id) if quote_id else None,
        "rate_snapshot_id": str(rate_snapshot_id) if rate_snapshot_id else None,
        "payment_date": payment_date.isoformat() if payment_date else None,
    }


def validate_idempotency_fingerprint(
    fingerprint: dict | None,
    obligation_id: UUID,
    period_id: UUID | None,
    strategy: str,
    source_account_id: UUID,
    amount: Decimal,
    currency: str,
    quote_id: UUID | None,
    rate_snapshot_id: UUID | None,
    payment_date: date | None,
    source_currency: str | None = None,
    existing_payments: list[ObligationPayment] | None = None,
) -> None:
    """
    Validates idempotency match. If any material parameter differs, raises 409 Conflict.
    Numerical decimal comparison avoids false conflicts between 500 and 500.00.
    Validates presence bidirectionally for quote and snapshot.
    """
    if fingerprint:
        if fingerprint.get("obligation_id") != str(obligation_id):
            raise HTTPException(status_code=409, detail="idempotency_conflict")
        if fingerprint.get("period_id") != (str(period_id) if period_id else None):
            raise HTTPException(status_code=409, detail="idempotency_conflict")
        if fingerprint.get("strategy") != strategy:
            raise HTTPException(status_code=409, detail="idempotency_conflict")
        if fingerprint.get("source_account_id") != str(source_account_id):
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        fp_amt = fingerprint.get("amount")
        if fp_amt is None or Decimal(str(fp_amt)) != Decimal(str(amount)):
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        if str(fingerprint.get("currency", "")).upper() != currency.upper():
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        fp_source_curr = fingerprint.get("source_currency")
        if source_currency and fp_source_curr and fp_source_curr.upper() != source_currency.upper():
            raise HTTPException(status_code=409, detail="idempotency_conflict")

        if fingerprint.get("quote_id") != (str(quote_id) if quote_id else None):
            raise HTTPException(status_code=409, detail="idempotency_conflict")
        if fingerprint.get("rate_snapshot_id") != (
            str(rate_snapshot_id) if rate_snapshot_id else None
        ):
            raise HTTPException(status_code=409, detail="idempotency_conflict")
        if fingerprint.get("payment_date") != (payment_date.isoformat() if payment_date else None):
            raise HTTPException(status_code=409, detail="idempotency_conflict")

    elif existing_payments:
        # Conservative check on historical records missing fingerprint
        for p in existing_payments:
            if p.obligation_id != obligation_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if p.account_id != source_account_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if period_id and p.obligation_period_id != period_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if p.currency.upper() != currency.upper():
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if quote_id and p.quote_id != quote_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if not quote_id and p.quote_id is not None:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if rate_snapshot_id and p.rate_snapshot_id != rate_snapshot_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if not rate_snapshot_id and p.rate_snapshot_id is not None:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            if (
                source_currency
                and p.source_currency
                and p.source_currency.upper() != source_currency.upper()
            ):
                raise HTTPException(status_code=409, detail="idempotency_conflict")

        total_p_amount = sum(p.amount for p in existing_payments)
        if Decimal(str(total_p_amount)) != Decimal(str(amount)):
            raise HTTPException(status_code=409, detail="idempotency_conflict")


async def find_replay_by_command_id(
    session: AsyncSession,
    user_id: UUID,
    command_id: UUID,
) -> tuple[object | None, list[ObligationPayment]]:
    """
    Finds existing financial event and associated obligation payments for a deterministic command ID.
    Strictly user-scoped and exact match.
    """
    from app.ledger.models import FinancialEvent

    stmt_ev = select(FinancialEvent).where(
        FinancialEvent.command_id == command_id,
        FinancialEvent.user_id == user_id,
    )
    existing_event = (await session.execute(stmt_ev)).scalars().first()
    if not existing_event:
        return None, []

    stmt_pay = (
        select(ObligationPayment)
        .where(
            ObligationPayment.financial_event_id == existing_event.id,
            ObligationPayment.user_id == user_id,
        )
        .order_by(ObligationPayment.created_at.asc())
    )
    payments = list((await session.execute(stmt_pay)).scalars().all())
    return existing_event, payments
