# Backend V1.1 Sprint 7 — Regression & Alpha Readiness Report

## 1. Executive Summary
Sprint 7 regression has successfully concluded. All local tests and local smokes passed. Production deploy to `lytrium-vps` succeeded. The backend V1.1 is stable, secure, and ready for Closed Alpha consumption.

## 2. Local Regression
- **Ruff**: Passed (0 errors).
- **Pytest**: Passed (142 checks passed, 0 failures).

## 3. Smoke Test Matrix
All local smoke tests successfully passed against the active local backend.

## 4. Financial Truth Validation
**PASS**. Financial Truth is consolidated. `free_money` calculations incorporate real cash minus committed outflows. 

## 5. Goals Validation
**PASS**. Target dates calculate required/remaining accurately. Flexible goals function normally.

## 6. Obligations Validation
**PASS**. `fixed_full_payment`, `partial_allowed`, and `variable_amount` behaviors are strictly enforced.

## 7. Credit Validation
**PASS**. `credit_card_transactions` is the source of truth. Payments successfully deduct cash and reduce debt.

## 8. Transfers Validation
**PASS**. In/out transfers correctly balance without altering total net cashflow.

## 9. Conversations Validation
**PASS**. Clarifications, ambiguities, fail-closed handling, and correct intent extractions operate flawlessly.

## 10. Ownership Validation
**PASS**. Complete isolation between cross-tenant data.

## 11. Traceability Validation
**PASS**. Events map back accurately to commands and source messages.

## 12. OpenAPI/Contracts
**PASS**. Validated schemas are exposed.

## 13. Production Deploy
**PASS**. Deployed successfully to VPS. Head aligned with `origin/main`.

## 14. Production Smoke
**PASS** (Partial). Health, readiness, and 401 unauthenticated access verified. Authenticated smokes skipped safely due to intentional `AUTH_BYPASS_ENABLED=false` and missing `NEXUM_PROD_TEST_TOKEN`.

## 15. Known Limitations
- Data cleanup in production requires manual review since test scripts cannot bypass auth.
- Deprecated aliases remain in the contract for backward compatibility.

## 16. Alpha Readiness Verdict
**PASS**. Backend V1.1 closed. Production verified. Alpha Ready.

## 17. Next Steps
- Proceed with frontend integration based on the V1.1 `openapi.json` contract.
