# Nexum Backend Agent Report
**Phase:** V1.7 FX Rate Snapshot Backend Refactor
**Date:** 2026-07-09
**Status:** Completed

## Objective
Implement the backend foundation for the **FX Rate Snapshot** architecture to enable the frontend to perform instantaneous FX previews without sending `source_amount`. The backend retains its role as the financial source of truth, validating the snapshot and calculating the real cross-currency amount upon payment confirmation.

## Actions Performed

1. **Schema & Model Updates**:
   - Confirmed the introduction of `rate_snapshot_id` to the `ObligationPayment` schema in V1.7 for persistence tracking.
   - Updated V1.7 payment payloads (`ObligationPeriodPaymentCreateRequest`, `ObligationFIFOPaymentCreateRequest`) to support optional `rate_snapshot_id` instead of `quote_id`.

2. **Refactored `app/obligations/service_v17.py`**:
   - Adjusted `_process_payment_fx` logic to rely exclusively on `rate_snapshot_id`.
   - The backend retrieves the locked `ExchangeRate` from the database via `rate_snapshot_id` to obtain the applicable FX rate.
   - Removed all dependencies on frontend-provided `source_amount`. The backend accurately calculates `source_amount = round(applied_amount / snapshot.rate, min_unit)`.

3. **API Contract Generation**:
   - Regenerated `openapi.json` to accurately reflect the V1.7 modifications in cross-currency payment structures, removing obsolete dependencies on `quote_id` and ensuring `rate_snapshot_id` is documented.

4. **Test Suite Integrity**:
   - Added unit tests in `tests/unit/test_fx_rate_snapshot.py` to validate:
     - Cross-currency payments with a valid `rate_snapshot_id` correctly debit the calculated `source_amount`.
     - Same-currency payments effectively bypass snapshot requirements.
     - Invalid or missing snapshots appropriately raise exceptions.
   - All tests run successfully.

5. **Legacy 500 Bug Clarification**:
   - Conducted a local investigation into the `GET /api/v1/obligations` 500 internal server error.
   - Tested the endpoint against all users in the active database and verified HTTP 200 SUCCESS in all cases.
   - Found that the defect was previously resolved in commit `b3a50c3` (`fix(obligations): tolerate v17 nullable fields in legacy responses`). Legacy schemas now tolerate null properties injected by V1.7 modifications.

## Next Steps
- Await approval to commit the FX Rate Snapshot refactoring branch.
- No frontend updates or production touches were made as per guidelines.
