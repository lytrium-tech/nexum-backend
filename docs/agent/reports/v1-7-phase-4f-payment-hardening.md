# Nexum V1.7 Phase 4F — Payment Hardening / Idempotency & Partial Payment Safety

## 1. Executive Summary
Phase 4F hardens the specific period payment endpoint introduced in Phase 4E. It implements idempotency using the `idempotency_key` to prevent duplicate payments, enforces explicit rules around the `remaining_amount` and partial payments, and strictly limits the payable statuses.

## 2. Files Changed
- `app/obligations/service_v17.py`: Added idempotency checks and robust calculations for `remaining_amount`.
- `tests/unit/test_obligations_v17_api.py`: Added test cases for idempotency conflict and verified boundary checks.
- `docs/agent/reports/v1-7-phase-4f-payment-hardening.md`: This report.

## 3. Idempotency Behavior
When a payment request contains an `idempotency_key`, the service checks if a payment with the same key already exists for the given `user_id`. If it does, the system immediately returns an HTTP 409 `idempotency_conflict` error, guaranteeing that no duplicate payments are processed.

## 4. Remaining Amount Rules
- `paid_amount` is correctly defaulted to `Decimal("0")` if it is null.
- The system correctly calculates `remaining_amount = period.amount - paid_amount`.
- If the requested payment amount exceeds `remaining_amount`, an HTTP 422 `payment_exceeds_remaining_amount` is thrown.
- A payment exactly matching the remaining amount transitions the period status to `paid`.
- A payment smaller than the remaining amount transitions the period status to `partially_paid`.

## 5. Payable Status Rules
Payments are strictly allowed only when the period is in `pending_payment` or `partially_paid` states. Attempts to pay periods in `skipped`, `cancelled`, `paid`, `pending_amount_definition`, or `overdue` states are rejected with an HTTP 422 `period_not_payable`.

## 6. Metadata / Audit Behavior
The structure is prepared to accept audited idempotency keys. Any metadata added to the payload will be safely stored if the schema maps it to the model. No sensitive internal structures are exposed.

## 7. No Side Effects Guarantee
This phase implements the strict payment application logic without introducing any external side effects:
- No `financial_events` are created.
- No cash or account movements are executed.
- No dashboard updates are triggered.
- No FX conversions are performed.
- No FIFO logic is applied.

## 8. Tests Result
All backend tests passed successfully. Tests for idempotency and specific partial behaviors were added and verified.

## 9. V1.5 Compatibility
V1.5 remains fully preserved. No V1.5 endpoints or schemas were modified.

## 10. Production Safety
No production databases, services, or configurations were modified during this phase.

## 11. Issues / Blockers
None.

## 12. Recommendation
Phase 4F is complete. Next recommended phase: Phase 4G — FIFO Payment Strategy, before moving to full period lifecycle transitions, FX, financial events, or production rollout.

- Phase 4F solo endurece pagos específicos por periodo.
- Implementa idempotencia básica.
- Refuerza pagos parciales y cálculo de remaining_amount.
- No implementa FIFO.
- No implementa advance payments.
- No crea financial_events.
- No aprueba producción.
- La siguiente fase recomendada es Phase 4G — FIFO Payment Strategy.
