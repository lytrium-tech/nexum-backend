# Nexum V1.7 Phase 4D — Define Variable Period Amount

## 1. Executive Summary
Phase 4D successfully implements the ability to define the amount of a variable period when its status is `pending_amount_definition`. This minimal mutation is strictly guarded by the `NEXUM_OBLIGATIONS_V17_ENABLED` feature flag and ensures robust validations without introducing complex lifecycle transitions or payment workflows yet.

## 2. Files Changed
- `app/obligations/schemas_v17.py`: Added `ObligationPeriodAmountDefineRequest` schema.
- `app/obligations/service_v17.py`: Added `define_period_amount` method enforcing domain rules.
- `app/obligations/router_v17.py`: Added `PATCH /api/v1.7/obligations/{obligation_id}/periods/{period_id}/amount`.
- `tests/unit/test_obligations_v17_api.py`: Added comprehensive tests covering validations, success transitions, and strict feature flag enforcement.

## 3. Endpoint Added
- `PATCH /api/v1.7/obligations/{obligation_id}/periods/{period_id}/amount`

## 4. Feature Flag Behavior
- **OFF**: Endpoint responds with `403 Forbidden` (`feature_flag_disabled`). Data is untouched.
- **ON**: Access granted to validation workflows.

## 5. Variable Period Amount Behavior
- For a period properly belonging to a variable obligation, defining the amount updates `amount` and transitions the period status seamlessly to `pending_payment`.
- Does not affect payments, cache balances, or financial events.

## 6. Validation Rules
- `period_not_variable`: Rejected if obligation `amount_type` is not variable.
- `period_amount_already_defined`: Rejected if the period is not in `pending_amount_definition`.
- `invalid_amount`: Handled via Pydantic (`amount > 0` required).
- `currency_mismatch`: Currency supplied must match the obligation currency.

## 7. User Ownership / Isolation
- The obligation must be successfully retrieved confirming explicit ownership by the authenticated user before proceeding to evaluate period updates. Mismatches yield `404 Not Found`.

## 8. Persistence Behavior
- Simply updates the `amount` and `status` scalar fields on the `ObligationPeriod` record. No side effect tables were modified.

## 9. OpenAPI Validation
- The endpoint and schema seamlessly serialize to standard OpenAPI specs alongside previous V1.7 schemas.

## 10. Tests Result
- Added `test_define_period_amount` achieving comprehensive coverage for validations and errors.
- Result: 239 passed, 3 skipped.

## 11. V1.5 Compatibility
- No V1.5 endpoints or behaviors were modified.

## 12. Production Safety
- No schemas or DB structures were modified in this phase. Database logic remains backward-compatible.

## 13. Issues / Blockers
- None.

## 14. Recommendation
Phase 4D is fully implemented. Next recommended phase: Phase 4E — Basic Specific Period Payment, before implementing partial payments, FIFO, FX, financial events, or full lifecycle transitions.
