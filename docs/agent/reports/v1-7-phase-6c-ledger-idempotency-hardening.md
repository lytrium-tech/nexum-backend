# Phase 6C - Ledger Idempotency Hardening

## Overview
Phase 6C focused on hardening idempotency for V1.7 obligation payments to ensure absolute safety when creating financial events in the ledger.

Prior to this phase, `command_id` fell back to a random UUID if `idempotency_key` was missing. This presented a financial risk because identical retries without an `idempotency_key` would generate unique `command_id`s, bypassing the database's `idx_financial_events_command_id` uniqueness constraint and allowing duplicate `financial_events` to be created.

## Changes Implemented

1. **Mandatory Idempotency Keys**:
   - Updated `ObligationV17Service.pay_specific_period` to strictly enforce `data.idempotency_key`. If absent, it now explicitly raises `HTTPException(422, "missing_idempotency_key")`.
   - Updated `ObligationV17Service.pay_fifo` to do the same.

2. **Deterministic `command_id` Generation**:
   - Replaced `uuid.uuid4()` fallback with `uuid.uuid5(uuid.NAMESPACE_OID, f"obligations_v1_7:{user_id}:{data.idempotency_key}")` for the `financial_event.command_id`.
   - `ObligationPayment.id` continues to use unique, random `uuid.uuid4()` for each row, ensuring FIFO payments with multiple allocations do not conflict on primary keys.

3. **Testing Hardening**:
   - Added unit tests `test_pay_specific_period_missing_idempotency_key` and `test_pay_obligation_fifo_missing_idempotency_key` to strictly verify that `422 Unprocessable Entity` is raised when the key is omitted.
   - Identified and resolved a 500 unhandled exception in the test harness by correctly mocking `.scalars().first()` for `AsyncSession.execute()` inside the missing idempotency test.
   - Verified that `ruff --unsafe-fixes` changes that touched legacy Enum structures outside the strict scope were safely reverted.
   - All tests passed.

## Integrity Assertions
- **Production Status**: untouched.
- **Ledger Invariants**: duplicate ledger event creation via missing idempotency keys is no longer possible for V1.7 obligation payments.
- **Frontend Contracts**: untouched, `openapi.json` remains strictly identical.
- **Ruff Format & Check**: 100% clean.

Phase 6C is complete and ready for final validation.
