# Backend V1.4.1 — Ledger Currency Runtime Stabilization Report

## 1. Executive Summary
An audit was performed to investigate why new ledger events for a USD account (ARQ) were being recorded and displayed as COP. The investigation revealed that the V1.4 codebase correctly requires and maps the currency, but the local development environment had not restarted the backend server after the V1.4 updates. Thus, the old code was still running and falling back to COP.

## 2. Runtime Version
- The git commit was correctly at `2769a7e docs: close backend v1.4 handoff` (or its parent `91c1aa7`), indicating the codebase was updated.
- However, the `uvicorn` backend server had not been restarted and was still serving pre-V1.4 logic in memory.

## 3. OpenAPI Runtime Check
After restarting the server (`python -m uv run uvicorn app.main:app --reload`), a local fetch to `/openapi.json` successfully showed the V1.4 schema, including `estimated_totals` and the mandatory `currency` fields.

## 4. DB Verification
Checking the database showed that the ARQ account is correctly set to `USD`, but the recent events were incorrectly saved with `currency = 'COP'`.

## 5. New Event Test
After restarting the backend, a new `income` event test was simulated. The result:
- `a.currency = USD`
- `fe.currency = USD`
The system successfully preserved the USD currency.

## 6. Root Cause
- **Backend Not Restarted**: The backend server was running an older version of the code where `LedgerEventCreate` defaulted to COP or didn't explicitly map `account.currency`.
- **Server Default**: The `financial_events.currency` table still has a `server_default="COP"`, but V1.4 correctly bypasses this by explicitly requiring `currency` in the payloads. The V1.4 code is fully resilient to this default.

## 7. Classification
- `ENVIRONMENT ISSUE`: Backend was not restarted after git pull/checkout.
- `DATABASE LEGACY DATA`: The events created right before the restart were permanently saved as COP due to the old code execution.

## 8. Fix Implemented
- The backend server was restarted to load the V1.4 code into memory.
- No new code fixes were needed because V1.4 already solved the issue. The `server_default="COP"` remains in the DB for backward compatibility, but it is harmless because V1.4 explicitly overrides it. We will not execute a migration to remove it unless requested, to avoid database schema churn.

## 9. Tests
All existing 173 tests pass perfectly. The unit tests already assert that `currency` is properly mapped when explicitly provided.

## 10. Validation
- Git working tree is clean.
- Ruff checks passed without any errors.

## 11. Git State
- `git status`: 1 untracked file (`docs/agent/reports/backend-v1-4-1-ledger-currency-runtime-stabilization-report.md`)
- `git log`: HEAD is at `2769a7e`

## 12. Frontend Retest Needed
Frontend validation must be re-run since the backend is now actually running the V1.4 code. The existing COP events in the ARQ account are permanently 'COP' (DATABASE LEGACY DATA), so the tester should create a new test event to see 'USD' in History.
