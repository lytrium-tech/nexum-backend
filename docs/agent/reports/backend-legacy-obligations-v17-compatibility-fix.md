# Backend Legacy Obligations V1.7 Compatibility Fix

## Overview
A `500 Internal Server Error` was encountered on the legacy endpoint `GET /api/v1/obligations`. This was caused by records created under the V1.7 schema having `NULL` values for fields that the V1.5/V1.6 `ObligationRead` schema expected to be non-null (`type`, `start_date`, `first_due_date`, `status`).

## Root Cause
- **Endpoint:** `GET /api/v1/obligations`
- **Exception:** `pydantic_core._pydantic_core.ValidationError: 4 validation errors for ObligationRead`
- **Cause:** V1.7 records introduced `NULL` values into columns expected to be populated by the legacy schema.

## Fix Implemented
- The `ObligationRead` schema in `app/obligations/schemas.py` was updated.
- The following fields were made nullable (`| None = None`):
  - `type`
  - `start_date`
  - `first_due_date`
  - `status`
- A new test `test_legacy_obligation_read_tolerates_null` was added to `tests/unit/test_obligations.py` to ensure `ObligationRead` successfully validates records with these `NULL` fields.

## Data Risks & Backfill
- **Data migration/backfill:** No data migration or backfill is recommended at this time, as V1.7 specifically allows these fields to be empty by design. Forcing a backfill would invent financial state that does not exist.
- **Risk of incomplete data:** The frontend will receive `null` for these fields on V1.7 records when accessed via legacy endpoints. The UI is expected to handle this gracefully (e.g., showing a fallback like "—").

## Validation
- `pytest tests/unit/test_obligations.py -v`: Passed.
- `pytest tests/unit/test_frontend_contracts.py -v`: Passed.
- `ruff check .`: Handled (all errors pre-existing and out of scope).
- Frontend lint and build executed successfully.
