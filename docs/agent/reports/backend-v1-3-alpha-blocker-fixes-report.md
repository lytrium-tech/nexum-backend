# Backend V1.3 — Alpha Blocker Fixes Report

## 1. Executive Summary
This report summarizes the implementation of Backend V1.3, which resolves five critical blockers affecting financial trust and lifecycle management discovered during Alpha QA. All fixes have been implemented, tested, and pass the existing test suite alongside new validation constraints.

## 2. Bugs Fixed
1. Ledger events lacking source currency.
2. `SnapshotTruth` naively summing multiple currencies in global fields.
3. `already_paid_this_period` triggering an incorrect "paid" state and lacking coverage metadata.
4. Obligations lacking a complete edit/archive lifecycle due to missing `is_active` support and overly strict read queries.
5. Accounts summary and list APIs having inconsistent parsing for `include_archived`.

## 3. already_paid_this_period / covered
- `period_status` can now return `"covered"`.
- When `already_paid_this_period` or `start_next_period` is provided, `coverage_reason = "already_paid_outside_nexum"` is injected into the metadata.
- Covered obligations safely report `remaining_amount = 0`, `is_pending = false`, and `period_status = "covered"` without deducting balances or creating fake payments.

## 4. Accounts include_archived
- The FastAPI router for `GET /accounts` now explicitly declares `include_archived: bool = Query(False)`.
- This ensures unambiguous query parsing for the frontend, restoring parity with the summary behavior.

## 5. Ledger Currency
- Removed the blind `"COP"` fallback.
- `create_income`, `create_expense`, `create_transfer`, `create_contribution`, `create_payment`, and `create_account` (opening balance) now explicitly read the `currency` from the source `Account` or `CreditCard` and map it to `LedgerEventCreate`.

## 6. Snapshot Multi-Currency
- `SnapshotTruth` now implements the "Passive multi-currency" directive by preventing unsafe mixed-currency sums.
- If multiple currencies are present in cash or cashflow, `calculation_warnings` receives `"cross_currency_global_totals_disabled"`.
- Under multi-currency scenarios, global legacy fields (`available_real`, `free_money`, etc.) are explicitly zeroed out to prevent dangerous misinterpretations, forcing the frontend to read `totals_by_currency`.
- Single-currency setups remain backward compatible.

## 7. Obligations Edit/Archive Lifecycle
- `ObligationUpdate` now accepts `is_active: bool | None`.
- `GET /obligations` explicitly supports `include_archived: bool = Query(False)`.
- `ObligationRepository.list_by_user` was rewritten to support an `include_archived` filter.
- These changes allow users to read, edit, and reactivate archived obligations gracefully without needing hard deletes.

## 8. OpenAPI Changes
- `openapi.json` has been successfully regenerated.
- `ObligationUpdate` now reflects `is_active`.
- `GET /obligations` and `GET /accounts` document `include_archived`.
- Schema changes for `LedgerEventCreate` and `SnapshotTruth` warnings are implicitly covered by Pydantic models.

## 9. Tests
- Tests were refactored to explicitly declare `currency="COP"` on mocked accounts to satisfy the stricter validation.
- The semantics of `test_obligations_semantics.py` were updated to assert `period_status == "covered"`.
- All 172 tests passed successfully. Ruff formatting and linting pass.

## 10. Known Limitations
- The decision to set global `SnapshotTruth` totals to zero when multiple currencies are present will break frontend displays that rely exclusively on global numbers. The frontend MUST be adapted to read from `totals_by_currency` as defined in the plan.
- FX conversions remain unsupported.

## 11. Final Status
Backend V1.3 Alpha Blocker Fixes is **IMPLEMENTED** and ready for commit.
