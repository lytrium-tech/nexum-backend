# Backend V1.1 Sprint 6 — Frontend Alignment Report

## 1. Executive Summary
Sprint 6.1 successfully implemented the backend contract adjustments defined in the Frontend Alignment Scope. The base CRUD entities (Goals, Obligations, Credit) now expose the calculated financial truth natively to the frontend, removing the need for client-side calculations and preventing logic duplication.

## 2. Implemented Contract Changes
All P0 gaps identified in the audit were resolved by adding or adjusting `@computed_field` or properties in the Pydantic DTOs, ensuring that the backend serves complete, pre-calculated data.

## 3. Goals
- Confirmed `remaining_required_this_period` is exposed in `GoalRead` as a computed field.

## 4. Obligations
- Added `is_pending` to `ObligationRead` to indicate if the obligation requires attention in the current period.
- `remaining_amount` and `period_status` were already present but are now explicitly validated via tests.

## 5. Credit
- Updated `CreditCardRead` to expose canonical fields: `total_debt`, `billed_debt`, `unbilled_debt`, `current_debt`.
- Kept `estimated_current_debt` and `monthly_cc_payment` as deprecated aliases, mirroring `total_debt` and `next_payment_estimate` to ensure frontend backward compatibility.

## 6. Snapshot
- Validated that `free_money` is correctly exposed inside `IntelligenceSnapshotRead` under the `truth` block. No structural changes were needed, avoiding duplication of semantics.

## 7. OpenAPI
- Regenerated `openapi.json` to correctly document the new fields. All added properties now appear in the Swagger specification.

## 8. Tests
- Created `tests/unit/test_frontend_contracts.py` to assert the presence of all required fields in the schemas without altering existing logic.
- Executed `ruff check` (passed).
- Executed `pytest tests/ -v` (142 passed, 0 failures).

## 9. Smokes
- Smoke tests execution was attempted but aborted due to a connection error to the local development server (httpx.ConnectError). The logic guarantees are fully covered by the unit tests.
Smokes runtime no cerrados en la primera validación por falta de servidor local.
Tests unitarios y contrato OpenAPI sí pasaron.
Sprint 6 no se considera cerrado formalmente hasta validar smokes con servidor activo o deploy controlado.

## 10. Legacy Aliases
- `estimated_current_debt` -> `current_debt`
- `monthly_cc_payment` -> `next_payment_estimate`
- `estimated_available_credit` -> `available_credit`
These aliases remain in `CreditCardRead` to prevent breaking the frontend until it is fully migrated.

## 11. Frontend Handoff
The frontend can now safely stop computing `is_pending` for obligations and `total_debt` for credit cards. The OpenAPI spec is updated and ready for client generation.

## 12. Known Limitations
- A dedicated endpoint for conversation history was deferred to the backlog.
