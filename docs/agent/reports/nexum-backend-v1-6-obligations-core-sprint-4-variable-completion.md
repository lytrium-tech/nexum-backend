# Nexum Backend V1.6 — Obligations Core Sprint 4 Variable Amounts & Completion Lifecycle

## 1. Executive Summary
This sprint implemented the definition of variable amounts for pending periods and the completion lifecycle engine. Obligations correctly transition to "completed" states based on their sequence types (one-time, end-count, end-date) once all necessary periods are paid or skipped, while maintaining backwards compatibility with Sprint 3's payment mechanisms.

## 2. Files Changed
- `app/obligations/schemas.py`: Added `ObligationPeriodAmountUpdate`.
- `app/obligations/service.py`: Added `define_amount` and `evaluate_lifecycle` methods. Updated other endpoints to trigger the lifecycle check.
- `app/obligations/router.py`: Exposed `PATCH /api/v1/obligations/periods/{period_id}/amount`.
- `tests/unit/test_obligations_lifecycle.py`: Created robust unit tests validating amount definitions and completion rules.
- `openapi.json`: Regenerated to reflect the new endpoints.

## 3. Variable Amount Definition
- A new `PATCH /periods/{period_id}/amount` endpoint allows users to define amounts for `pending_amount_definition` periods.
- Prevents amounts lower than already paid.
- Rejects modification of amounts on fixed obligations or periods already `paid`, `skipped`, or `cancelled`.
- Triggers a period state transition to `pending_payment` (or `overdue` if due date is past).

## 4. Completion Lifecycle Rules
- **One Time:** Completes as soon as its only period is resolved.
- **End Count:** Completes once all the periods up to `end_count` are resolved.
- **End Date:** Completes if all generated periods are resolved and the next generated period's start bounds exceed `end_date`.
- **Indefinite:** Remains active indefinitely.
- Lifecycle is transparently checked after `sync_periods`, `skip_period`, `define_amount`, and payments.

## 5. Skipped Period Handling
- Skipping a period is now treated as "resolved" for the sake of the lifecycle, accurately simulating periods that are no longer owed without wiping history.

## 6. Payment/FIFO Compatibility
- The changes were carefully designed to preserve `pay_specific_period` and `pay_obligation_fifo` logic from Sprint 3. The lifecycle evaluation was hooked into the return path ensuring payments work transparently.

## 7. API Impact
- **New Route:** `PATCH /api/v1/obligations/periods/{period_id}/amount`
- The `openapi.json` contract was updated. No backward breaking changes to existing endpoints.

## 8. Tests
- Total tests passed: 224
- Added 9 new tests explicitly targeting `define_amount` validations and completion lifecycle mechanics via mocks matching the new domain logic.

## 9. Skipped Tests Status
- **Skipped before:** 22
- **Skipped after:** 22
- **Reactivated:** 0 (Old obligation logic in `test_multicurrency_qa.py` is safely replaced by modern models in `test_obligations_payment.py` and left skipped for clean removal during the upcoming stabilization sprint rather than forcing redundant retrofits).

## 10. Known Limitations
- The older conversational logic still relies on deprecated semantic pathways and remains skipped.
- Frontend contracts are not yet modernized for `ObligationPeriod` usage and need V1.6 adjustments.

## 11. Next Sprint Recommendation
The backend structural core is fully functional. We strongly recommend moving to **Sprint 5: Frontend Contracts & Stabilization** to update `frontend` logic, reactivate or remove older QA tests, and prepare for release.
