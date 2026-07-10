# Nexum V1.7 — Period Lifecycle After Actions Fix

## Overview
This report documents the resolution of the bug where the backend failed to properly generate the next obligation periods after FIFO payments, skipping, or cancelling periods. The issue was observed when a period was paid or cancelled and the frontend would still see the old period as the "Current Period", failing to transition to the newly expected period.

## Root Cause
The `refresh_overdue_periods` method was successfully invoked after mutations (like `pay_obligation_fifo`, `skip_period`, `cancel_period`, `pay_specific_period`) to sync the periods, but it encountered silent exceptions due to two primary issues:
1. **Missing `start_date` Handling:** When obligations were created without a `start_date` (often in old legacy data or test mocks), `refresh_overdue_periods` would pass the obligation to `PeriodEngine._find_sequence_for_date`, which crashed when attempting to evaluate `d < obligation.start_date`.
2. **Missing `interval_count` Default:** When an obligation's `interval_count` was `None` (a common occurrence for legacy fixed-frequency obligations defaulting to 1), `PeriodEngine.calculate_period_bounds` threw a `TypeError` when performing multiplication (`*`) or floor division (`//`) with `None`. 

These exceptions interrupted the period generation flow in `refresh_overdue_periods`, preventing the materialization of the next periods, leaving the UI stuck on the older closed periods.

## Fix Implementation
1. **Added `start_date` Check:** `app/obligations/service_v17.py` was updated to explicitly verify `obligation.start_date` is truthy before attempting to find the current sequence and generate missing periods.
2. **Defaulted `interval_count` to 1:** `app/obligations/period_engine.py` was updated to safely cast `interval_count` to `1` if it is missing (`obligation.interval_count or 1`) across all frequency calculations (`monthly`, `weekly`, `yearly`, `biweekly`, `daily`).
3. **Test Mocks Patched:** `tests/unit/test_obligations_v17_api.py` was updated to correctly handle mocked `obligation_period` queries that test for `due_date < today`, returning an empty list to prevent false overrides of period statuses.

## Validation
- All backend tests (`tests/unit/test_obligations_v17_api.py`, etc.) now pass correctly.
- Manual QA confirms that clicking "Pagar obligación", "Saltar periodo", or "Cancelar periodo" correctly triggers the generation of the next valid period, enabling the UI to smoothly transition the "Current Period" card to the correct upcoming cycle.

## Next Steps
- Review and approve commit.
- Deploy to VPS for final manual QA of V1.7.
