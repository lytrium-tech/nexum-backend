# Nexum V1.7 Phase 4G — FIFO Payment Strategy

## 1. Executive Summary
Phase 4G implements the FIFO payment strategy for obligations in V1.7. It introduces the ability to pay an obligation globally, distributing the payment amount across its payable periods, starting from the oldest due date to the most recent.

## 2. Files Changed
- `app/obligations/schemas_v17.py`: Added `ObligationFIFOPaymentCreateRequest` and `ObligationFIFOPaymentResultResponse` to validate and serialize FIFO payments.
- `app/obligations/service_v17.py`: Added `pay_obligation_fifo` containing the logic to traverse periods and allocate payments.
- `app/obligations/router_v17.py`: Added `POST /api/v1.7/obligations/{obligation_id}/payments` endpoint.
- `tests/unit/test_obligations_v17_api.py`: Extended tests to cover FIFO distribution, idempotency, overpayments, and status updates.
- `docs/agent/reports/v1-7-phase-4g-fifo-payment-strategy.md`: This report.

## 3. Endpoint Added
`POST /api/v1.7/obligations/{obligation_id}/payments`

## 4. FIFO Strategy
The implementation fetches all payable periods and orders them strictly by:
1. `due_date.asc()`
2. `sequence_number.asc()`
3. `created_at.asc()`
It iterates sequentially through these ordered periods, applying portions of the total payment amount to each until the amount is depleted.

## 5. Period Selection Rules
Only periods with statuses `pending_payment` or `partially_paid` are selected. Periods with statuses `skipped`, `cancelled`, `paid`, `pending_amount_definition`, or `overdue` are ignored and blocked from payments. If no payable periods exist, an HTTP 422 `no_payable_periods` is raised.

## 6. Payment Allocation Rules
For each matched period, a `remaining_amount` is determined via `amount - paid_amount`. 
A `payment_slice` is derived using `min(remaining_input_amount, remaining_amount)`.
If `paid_amount` equals `amount`, the status becomes `paid`; if smaller, `partially_paid`.
A unique `ObligationPayment` is created for each period that receives an allocation.

## 7. Idempotency Behavior
Idempotency keys are explicitly checked against `ObligationPayment` records for the user. If an existing idempotency key is detected, the system immediately returns an HTTP 409 `idempotency_conflict`, matching the Phase 4F approach. No duplicate operations occur.

## 8. Overpayment / No Advance Payment Rule
If the requested payment amount exceeds the aggregated `total_remaining_amount` across all payable periods, the transaction is forcefully blocked via HTTP 422 `payment_exceeds_total_remaining_amount`. No funds are captured, and no advance payments are executed.

## 9. No Side Effects Guarantee
No side effects have been introduced:
- No `financial_events` are created.
- No actual cash/ledger systems are touched.
- No dashboard systems are updated.

## 10. OpenAPI Validation
OpenAPI schema has naturally inherited the new endpoints and models; the runtime API handles validation properly without requiring static schema commits. V1.5 tests remain completely unaffected.

## 11. Tests Result
All backend unit tests passed successfully. The test suite validated FIFO behavior (partial hits, exact hits, overpayments, skipped periods, idempotency errors).

## 12. V1.5 Compatibility
V1.5 is preserved entirely. None of the V1.5 logic or routes were touched.

## 13. Production Safety
No production database schemas or states were touched or migrated. Everything operates exclusively behind `NEXUM_OBLIGATIONS_V17_ENABLED`.

## 14. Issues / Blockers
None.

## 15. Recommendation
Phase 4G is complete. Next recommended phase: Phase 4H — Period Lifecycle / Status Transitions, to close Core Obligations V1.7 before moving to FX, Financial Events, Intelligence, Frontend, or Production Rollout.

- Phase 4G implementa pago general FIFO.
- No implementa advance payments.
- No implementa FX.
- No crea financial_events.
- No aprueba producción.
- La siguiente fase recomendada es Phase 4H.
- Phase 4H cerrará Fase 4 — Core Obligations V1.7.
