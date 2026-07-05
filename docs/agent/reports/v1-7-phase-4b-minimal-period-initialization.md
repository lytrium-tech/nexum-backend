# Nexum V1.7 Phase 4B — Minimal Period Initialization

## 1. Executive Summary
Phase 4B successfully implements the initialization of the first `obligation_period` when a V1.7 obligation is created. This extension respects the `NEXUM_OBLIGATIONS_V17_ENABLED` feature flag and maps the period structure to the base V1.5 fields safely, without introducing any external complexity such as advanced generation schedules, FIFO logic, or partial payments.

## 2. Files Changed
- `app/obligations/service_v17.py`: Added the `_create_initial_period` method to generate the first period synchronously with the obligation creation.
- `app/obligations/router_v17.py`: Updated tuple unpacking to receive the initial period returned by the service layer, and updated the dependency injection of the user to `AuthenticatedIdentity`.
- `app/obligations/schemas_v17.py`: Refined model configurations via Pydantic to ensure attribute compatibility.
- `tests/unit/test_obligations_v17_api.py`: Implemented robust assertions validating the database mock calls for proper period tracking and correct attribute mappings depending on amount types.

## 3. Period Creation Behavior
- Only **one** period is generated upon creation.
- Further recurrences are completely out of scope and explicitly untouched.
- `period_key` defaults to the `YYYY-MM` derived from `first_due_date`.
- `sequence_number` initialized to `1`.
- `is_current` explicitly set to `True`.

## 4. Fixed Amount Behavior
- Amount maps exactly to the `base_amount` of the obligation.
- Status is securely initialized as `pending_payment`.

## 5. Variable Amount Behavior
- Amount maps to `None`, respecting the semantic value for uncertain future values.
- Status is safely initialized as `pending_amount_definition`.

## 6. Feature Flag Behavior
- When `NEXUM_OBLIGATIONS_V17_ENABLED` is `False`, all creations (including the period generation) are strictly blocked at the router level.
- When `True`, the creation payload successfully propagates to the service to establish the obligation and the single period atomically.

## 7. Persistence Behavior
- Pushed synchronously to the mock/real database through SQLAlchemy `session.add`.
- Does not affect or mutate V1.5 balance logic or legacy views. No financial events generated.

## 8. Tests Result
- `pytest tests/ -v` resulted in a full suite pass.
- Targeted unit tests verify object counts inside the session buffer.

## 9. V1.5 Compatibility
- Untouched. `models.py` legacy data integrity remains guaranteed by restricting all period initialization details to strictly standard data types matching `ObligationPeriod`.

## 10. Production Safety
- No automated database migrations applied.
- No direct alterations to live staging/production tables.
- All actions were scoped entirely locally and validated by tests.

## 11. Issues / Blockers
- None encountered. 

## 12. Recommendation
Next recommended phase: Phase 4C — Period Read Consistency & Detail Endpoints, before implementing full lifecycle/status transitions.

This clarifies that:
- Phase 4B solo inicializa un periodo mínimo.
- No implementa lifecycle completo.
- No implementa pagos.
- No implementa FIFO.
- No aprueba producción.
- El siguiente paso recomendado es validar lectura/consistencia de periodos antes de mutaciones adicionales.
