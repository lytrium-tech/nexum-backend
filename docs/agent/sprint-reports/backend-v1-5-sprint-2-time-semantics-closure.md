# Backend V1.5 Sprint 2 — Goals & Obligations Time Semantics Closure

## 1. Executive Summary
This document summarizes the completion of Backend V1.5 Sprint 2. The core objective was to implement time-based dynamic semantics for Goals and Obligations to calculate correct period statuses directly on-read without requiring background cron jobs or automated database mutations.

## 2. Scope Implemented
- Dynamic time-based calculation for `GoalRead` schema properties.
- Dynamic time-based calculation for `ObligationRead` schema properties.
- OpenAPI contract update reflecting the new schema properties.
- Frozen-time test suite extensions to validate time semantics.
- No cron jobs and no automatic state mutations introduced.
- `freezegun` dev dependency added safely to mock timezone-aware datetime.

## 3. Goals Time Semantics
- Added dynamic calculation for `period_status` verifying if a goal is overdue (`target_date` in the past).
- Accurate generation of daily requirements (`daily_required_this_period`) rounding upwards according to currency rules.
- Retained states: `pending`, `partial`, `covered`, `overfunded`, `completed`, `flexible`.

## 4. Obligations Time Semantics
- `is_pending` now dynamically evaluates to `True` if `period_status` is `pending`, `partial`, or `overdue`.
- Added dynamic computation of `next_due_date` utilizing the current month/year and explicit bounds checking for trailing days (e.g., month ending).
- Added `days_until_due` computation as integer delta from `next_due_date`.
- Extended `period_status` with robust classification including `overdue` if today exceeds the established due date.

## 5. OpenAPI Impact
- `GoalRead` and `ObligationRead` components in `openapi.json` have been synchronized.
- The interface contracts accurately reflect read-only fields computed natively by the backend service.

## 6. Tests
- 192 unit tests successfully passed (`tests/unit/`).
- Introduced explicit tests for goals and obligations using `@freeze_time` from the `freezegun` library to assert deterministic behaviors relative to local time (America/Bogota logic handling bounds and day offsets).

## 7. Regressions
- Zero regression incidents reported. Legacy schema behavior persists reliably. All out-of-scope logic and files altered dynamically by formatters (e.g., `app/accounts/service.py`) were manually reverted to maintain minimal operational surface area.

## 8. Known Limitations
- The `next_due_date` property implementation utilizes a simplistic assumption limiting to current monthly scopes bounded correctly via Python `calendar` modules; complex multimonth bounds or alternative frequencies may need deeper adjustments in upcoming sprints.
- Freezegun overrides native C-module clocks; test performance must be monitored if applied overly broadly instead of scoped to pure logical modules.

## 9. Frontend Impact
- Frontend V1.5 must map the augmented `ObligationRead.period_status` (`pending`, `partial`, `overdue`, `inactive`, `paid`, `covered`) against visual cues.
- `days_until_due` and `next_due_date` allow Frontend to natively display deadlines without doing custom offset logic.
- `GoalRead.period_status` `overdue` must be visually prioritized.

## 10. Next Step
- Finalize phase lock and commit Backend V1.5 Sprint 2.
- Execute deployment phase if authorized.
- Handoff OpenAPI schemas and frontend adaptations.
