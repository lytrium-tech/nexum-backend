# Phase 4E — Basic Specific Period Payment Completion Report

## Objective
Implement the first minimal V1.7 payment flow: registering a payment against a specific `obligation_period`.

## Changes Made
- Added `ObligationPeriodPaymentCreateRequest` and `ObligationPeriodPaymentResultResponse` schemas in `schemas_v17.py`.
- Implemented `pay_specific_period` method in `ObligationV17Service` to process the payment logic.
  - Returns 404 for missing obligation/period.
  - Returns 422 if period amount is not defined.
  - Returns 422 if period is not payable.
  - Returns 422 if payment amount exceeds remaining period amount.
  - Returns 422 if currency mismatch.
  - Creates exactly 1 `ObligationPayment` and updates `paid_amount` and `status` of the period to `partially_paid` or `paid`.
- Added `POST /api/v1.7/obligations/{obligation_id}/periods/{period_id}/payments` to `router_v17.py` behind the `NEXUM_OBLIGATIONS_V17_ENABLED` feature flag.
- Added `test_pay_specific_period` to `tests/unit/test_obligations_v17_api.py` validating partial payments, exact payments, overpayments, already paid periods, and disabled endpoints.
- Exported updated OpenAPI spec to reflect the new endpoint.

## Validations
- **Production DB untouched:** Yes.
- **Frontend untouched:** Yes.
- **Tests pass:** Yes, all backend tests pass.
- **FIFO implemented:** No.
- **Advance payments implemented:** No.
- **Financial Events created:** No.
- **Endpoints V1.5 broken:** No.

## Next Steps
Next recommended phase: Phase 4F — Partial Payment Support / Payment Hardening, before implementing FIFO, advance payments, FX, financial events, or full lifecycle transitions.

- Phase 4E solo implementa pago contra periodo específico.
- No implementa FIFO.
- No implementa advance payments.
- No crea financial_events.
- No aprueba producción.
- La siguiente fase recomendada es Phase 4F, no Phase 5.
