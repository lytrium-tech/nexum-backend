# Backend V1.6.2 - Payment Mode Compatibility Report

## Overview
During V1.6.2 QA, it was discovered that some obligations were created using legacy `payment_mode` enums: `partial_allowed` and `variable_amount`.
Because the `PeriodEngine` and `define_amount` operations strictly expected `fixed` and `variable`, this led to bugs where fixed obligations generated empty periods in status `pending_amount_definition`.

## Applied Fixes
- `PeriodEngine` now treats `partial_allowed` and `fixed_full_payment` as `fixed`.
- `define_amount` now treats `variable_amount` as `variable`.
- This ensures that existing obligations in the database will not break runtime operations.

## Data Strategy
- No manual data deletion or database modification will be performed without explicit approval.
- New obligations created by the frontend will send the correct `fixed` and `variable` enums.
- If old data with `pending_amount_definition` remains inconsistent, a non-destructive repair script or migration will be proposed in a future phase.
