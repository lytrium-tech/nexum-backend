# Backend V1.6.1 — Intelligence Snapshot Fix

## 1. Executive Summary
The Intelligence snapshot feature, particularly `get_obligations_metrics` and `get_pending_obligations`, was broken in production due to the migration to Obligations Core V1.6. The legacy repository relied on `obligations.amount` and the `v_pending_obligations_current_month` SQL view, both of which are incompatible with the new V1.6 models where financial state resides exclusively in `obligation_periods`.

This fix updates the intelligence repository to query `obligation_periods` directly.

## 2. Root Causes Fixed
- **`obligations.amount` dependency:** `get_obligations_metrics` no longer assumes `amount` exists on the `obligations` table.
- **Legacy SQL view dependency:** `get_pending_obligations` no longer reads from `public.v_pending_obligations_current_month`.

## 3. Implementation Details
- **ObligationPeriod Source of Truth:** Replaced raw queries with JOINs against `obligation_periods op` and `obligations o`.
- **Pending Statuses Included:** Queries filter specifically for `op.status IN ('pending_payment', 'partially_paid', 'overdue')`.
- **Closed Statuses Excluded:** `paid`, `skipped`, and `cancelled` are implicitly excluded by the status filter.
- **pending_amount_definition Handling:** Periods with `amount IS NULL` are safely excluded from the aggregations to avoid mathematical errors (e.g., `COALESCE(SUM(op.amount), 0)` operations).

## 4. Tests Added
New repository integration tests were added in `tests/unit/test_intelligence_repository.py`:
- `test_get_obligations_metrics_v16_model`: Verifies the query maps cleanly without legacy fields and calculates `pending_count` and `pending_amount` correctly.
- `test_get_pending_obligations_v16_model`: Verifies the query maps cleanly without the legacy view, properly joins the tables, and filters out closed periods.

## 5. OpenAPI
No changes to public API schemas were required. The contracts returned by the repository match the internal representations expected by `IntelligenceService`.
