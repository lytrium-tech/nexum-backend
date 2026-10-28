import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.credit.exceptions import CreditIdempotencyConflictError


def _canonical_decimal_str(val: Decimal | str | float | None) -> str | None:
    if val is None:
        return None
    d = Decimal(str(val))
    # Normalize: remove trailing zeros and exponent if integer
    normalized = d.normalize()
    if normalized == normalized.to_integral():
        return f"{normalized:.2f}"
    return format(normalized, "f")


def build_credit_fingerprint(
    *,
    user_id: UUID | str,
    operation_type: str,
    credit_card_id: UUID | str,
    booked_amount: Decimal | str | float,
    booked_currency: str,
    target_id: UUID | str | None = None,
    original_amount: Decimal | str | float | None = None,
    original_currency: str | None = None,
    fx_rate: Decimal | str | float | None = None,
    fx_quote_id: UUID | str | None = None,
    fx_rate_snapshot_id: UUID | str | None = None,
    payment_date: date | str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, canonical credit operation fingerprint dictionary."""
    return {
        "user_id": str(user_id),
        "operation_type": str(operation_type).lower(),
        "credit_card_id": str(credit_card_id),
        "target_id": str(target_id) if target_id else None,
        "booked_amount": _canonical_decimal_str(booked_amount),
        "booked_currency": str(booked_currency).upper(),
        "original_amount": _canonical_decimal_str(original_amount),
        "original_currency": str(original_currency).upper() if original_currency else None,
        "fx_rate": _canonical_decimal_str(fx_rate),
        "fx_quote_id": str(fx_quote_id) if fx_quote_id else None,
        "fx_rate_snapshot_id": str(fx_rate_snapshot_id) if fx_rate_snapshot_id else None,
        "payment_date": payment_date.isoformat()
        if isinstance(payment_date, date)
        else (str(payment_date) if payment_date else None),
    }


def hash_credit_fingerprint(fingerprint: dict[str, Any]) -> str:
    """Generate SHA-256 hash of canonical sorted JSON fingerprint."""
    canonical_json = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def validate_credit_fingerprint(
    fingerprint: dict[str, Any] | None,
    *,
    user_id: UUID | str,
    operation_type: str,
    credit_card_id: UUID | str,
    booked_amount: Decimal | str | float,
    booked_currency: str,
    target_id: UUID | str | None = None,
    original_amount: Decimal | str | float | None = None,
    original_currency: str | None = None,
    fx_rate: Decimal | str | float | None = None,
    fx_quote_id: UUID | str | None = None,
    fx_rate_snapshot_id: UUID | str | None = None,
    payment_date: date | str | None = None,
) -> None:
    """Validate that incoming operation parameters match existing fingerprint.

    Raises CreditIdempotencyConflictError if any material parameter differs.
    """
    if not fingerprint:
        return

    expected = build_credit_fingerprint(
        user_id=user_id,
        operation_type=operation_type,
        credit_card_id=credit_card_id,
        booked_amount=booked_amount,
        booked_currency=booked_currency,
        target_id=target_id,
        original_amount=original_amount,
        original_currency=original_currency,
        fx_rate=fx_rate,
        fx_quote_id=fx_quote_id,
        fx_rate_snapshot_id=fx_rate_snapshot_id,
        payment_date=payment_date,
    )

    mismatches: dict[str, dict[str, Any]] = {}
    for key, expected_val in expected.items():
        actual_val = fingerprint.get(key)
        # Decimal comparison for amounts and rates
        if key in ("booked_amount", "original_amount", "fx_rate"):
            if expected_val is None and actual_val is None:
                continue
            if expected_val is None or actual_val is None:
                mismatches[key] = {"expected": expected_val, "actual": actual_val}
            elif Decimal(str(expected_val)) != Decimal(str(actual_val)):
                mismatches[key] = {"expected": expected_val, "actual": actual_val}
        else:
            if expected_val != actual_val:
                mismatches[key] = {"expected": expected_val, "actual": actual_val}

    if mismatches:
        raise CreditIdempotencyConflictError(
            message="Idempotency key collision with conflicting parameters",
            detail={"mismatches": mismatches},
        )
