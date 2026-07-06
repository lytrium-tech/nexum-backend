# Nexum V1.7 Phase 6A — Financial Events / Ledger Baseline Audit

## 1. Executive Summary
This audit reviews the existing Ledger architecture and its `financial_events` schema to safely plan the integration of V1.7 Core Obligations payments. The system already features a centralized `LedgerService` and a robust idempotency constraint at the database level. Integrating V1.7 requires reusing this service within a single transaction boundary, ensuring that multi-period FIFO payments aggregate into a single ledger event, and capturing FX artifacts without mutating the legacy ledger table structures.

## 2. Current Ledger Architecture
The ledger domain resides in `app/ledger/`. It includes:
- `LedgerService` (`app/ledger/service.py`) for business rules.
- `LedgerRepository` (`app/ledger/repository.py`) which manages SQLAlchemy writes and gracefully handles idempotency violations via `IntegrityError` savepoints.
- Enums (`app/ledger/enums.py`) strictly mirroring PostgreSQL `CHECK` constraints.

## 3. Existing financial_events Structure
The `financial_events` table (mapped in `app/ledger/models.py`) strictly enforces:
- Amount > 0 (`financial_events_amount_check`)
- Valid directions (`financial_events_direction_check`)
- Valid event types (`financial_events_type_check`)
- Partial UNIQUE constraint on `command_id` (`idx_financial_events_command_id`) for idempotency.
It inherently stores `amount` and `currency`. For cross-currency scenarios, it does not currently have native FX columns (`fx_rate`, `source_amount`, etc.), relying on the JSONB `metadata` field.

## 4. Existing RPCs / Services / Routes
- `LedgerService.record_event(event_data: LedgerEventCreate)` is the central point of integration.
- Routes from `/api/v1/cash`, `/api/v1/credit`, and `/api/v1/accounts` actively call this service.
- There are no legacy RPC functions like `record_cash_event` in isolation; everything operates through `LedgerService`.

## 5. Current Obligation Payment Event Behavior
In V1.5 (`app/obligations/service.py`), paying an obligation directly calls `LedgerService.record_event(...)`. It maps the target amount and currency, sets the `event_type` to `obligation_payment`, and uses the `idempotency_key` as `command_id` to prevent double-charging. The resulting `financial_event.id` is then foreign-keyed back to `ObligationPayment`.

## 6. FX Artifacts and Ledger Compatibility
Currently, `financial_events` is not explicitly structured for V1.7 FX (it lacks `quote_id`, `source_amount`, `fx_rate` natively). However, these are safely stored in `obligation_payments`. To maintain full traceability in the ledger without altering the DB schema, FX artifacts should be embedded in the `metadata` JSONB column of `financial_events`.

## 7. Idempotency and Transaction Safety
`LedgerRepository` already implements a `begin_nested()` block to catch `IntegrityError` if `command_id` is repeated, returning an `idempotent=True` flag. This works natively with V1.7’s `idempotency_key`.

## 8. Risks
- **Non-transactional propagation:** If V1.7 creates `ObligationPayment` and `financial_events` across different uncoordinated sessions, a crash mid-flight will result in a ghost payment. Both must share the same `AsyncSession`.
- **FIFO Multi-Event Ghosting:** If a FIFO payment covering 3 periods creates 3 `financial_events`, the user's bank account will show 3 deductions instead of 1 aggregated deduction, breaking UX and reconciliation.

## 9. Design Questions Answered
1. **¿ObligationPayment V1.7 debe crear financial_event directamente o mediante servicio/adapter?**
   Mediante `LedgerService.record_event`, manteniendo el diseño actual de dominio.
2. **¿Debe existir un LedgerService reusable?**
   Ya existe en `app/ledger/service.py` y debe usarse.
3. **¿Specific period payment y FIFO deben crear uno o varios financial_events?**
   Deben crear **un solo** `financial_event`. Aunque internamente haya múltiples payment slices, para la cuenta origen es un solo movimiento de dinero (outflow).
4. **En FIFO multi-period, ¿debe crearse un financial_event por payment slice o uno agregado?**
   Uno agregado para representar fielmente el movimiento en la cuenta del usuario.
5. **¿Cómo se debe manejar FX en ledger?**
   Guardando los detalles (`source_amount`, `source_currency`, `fx_rate`) dentro del JSONB `metadata` de `financial_events`.
6. **¿Debe guardarse quote_id en financial_events o solo en obligation_payments?**
   Primariamente en `obligation_payments`, pero por trazabilidad se debe adjuntar al `metadata` de `financial_events`.
7. **¿Cómo se preserva idempotency entre payment y financial_event?**
   El `idempotency_key` del request debe inyectarse directamente en `LedgerEventCreate.command_id`.
8. **¿Cómo se evita duplicar eventos si se reintenta el request?**
   `LedgerRepository` atrapará el `IntegrityError` en la base de datos (vía `idx_financial_events_command_id`) y evitará la duplicación automáticamente.
9. **¿Qué debe pasar si payment se crea pero ledger falla?**
   Ambas operaciones deben compartir la misma `AsyncSession` y abortar conjuntamente (Rollback transaccional).
10. **¿La integración debe ser transaccional en una sola DB session?**
    Sí. Todo el proceso V1.7 debe usar la misma `AsyncSession`.

## 10. Recommended Phase 6B Scope
Implement the actual connection in a secure, small increment:
- Refactor `app/obligations/service_v17.py` to instantiate and use `LedgerService`.
- Ensure V1.7 payments (`pay_specific_period` and `pay_obligation_fifo`) aggregate their target amounts to dispatch a single `LedgerEventCreate`.
- Phase 6B should integrate LedgerService into ObligationV17Service using the same AsyncSession. FIFO payments should create one consolidated financial_event per request, while obligation_payments remain one row per affected period.
- FX artifacts should be stored in financial_events.metadata for now, without altering the core ledger schema.
- Use the shared `self.session`.
- Write rigorous unit tests simulating Ledger integration without touching the real database.

## 11. Production Safety
Phase 6A is purely analytical. No production databases, schemas, endpoints, or variables have been modified. 

## 12. Issues / Blockers
No blockers found. The existing `LedgerService` and idempotency constraints are perfectly suited for V1.7 integration.
