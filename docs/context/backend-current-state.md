# Backend Current State

## Repository State
- **Checkout actual:** rama `goals-v1-phase2-releases`
- **Repository HEAD:** `7f1f6caf3e5bf450a18e5aa3273a2d00edf34c44`
- **origin/main:** `7f1f6caf3e5bf450a18e5aa3273a2d00edf34c44`
- **Cambios locales:** gap V1.8 `reservations_by_account` y cambios preexistentes ajenos preservados; staging vacío.

## Repository vs Production
- **Repository Current State:** `HEAD` y `origin/main` sincronizados en `7f1f6caf3e5bf450a18e5aa3273a2d00edf34c44`.
- **Production:** Metas V1 Fase 2 está desplegada.
- **Migración productiva de Metas:** `goals_v1_ph2_releases`.

## Backend Purpose
- Backend financiero de Nexum.
- Construido con FastAPI, SQLAlchemy, Pydantic, y PostgreSQL (Supabase).
- El backend es la única fuente de verdad financiera.

## Core Principle
- **Backend calculates.** El backend es responsable de todas las matemáticas y lógica de negocio.
- **Frontend represents and previews.** El frontend muestra la información y estima valores visuales temporales.
- **LLM explains.** El LLM explica pero no calcula dinero ni autoriza transacciones.

## Active Subsystems
- Accounts and ledger.
- Credit cards V1.5.
- Goals.
- Obligations period engine.
- FIFO and Smart Payment.
- FX Engine and FX Rate Snapshot.
- Summary and intelligence context.
- Authentication and authorization.

## Goals Current State
- **Goals V1 Phase 1 (producción):** Bloques 1–3 desplegados con la migración `goals_v1_ph1_reserved`.
- **Características operativas:**
  - Idempotencia determinística (Fingerprint SHA-256 + Command ID).
  - Eventos de Ledger de tipo `neutral` para aportes a metas.
  - Atomicity en una única UoW (nested transactions y order locking Account -> Goal).
  - Cálculo de `goal_reserved_amount` integrado al cash balance.
  - Cálculo de `available_balance` aislando la porción reservada.
  - Paginación e historial HTTP consolidado entre transacciones nativas y legacy (`/api/v1/goals/{goal_id}/transactions`).
  - Inteligencia conciliada sin duplicar saldos entre sources ni considerar neutrales como cashflow.
- **Goals V1 Phase 2 (producción):**
  - Fase 2 ("Releases") implementada y desplegada en producción.
  - Commit productivo: `7f1f6caf3e5bf450a18e5aa3273a2d00edf34c44`
  - Migración aplicada: `goals_v1_ph2_releases`.
  - Endpoint `POST /api/v1/goals/{goal_id}/releases` disponible y documentado en OpenAPI.
  - Release Gate aprobado y cero drift post-despliegue validado.
  - Handoff detallado creado en `../docs/handoff/backend/backend-to-frontend-v1-8.md`.
  - Gap V1.8 local: `reservations_by_account` expuesto únicamente en Goal Detail mediante una agregación autoritativa por cuenta. OpenAPI local actualizado.
  - Las cuentas inactivas con reserva permanecen visibles con `account_is_active = false`; los releases desde cuentas inactivas continúan rechazados.
  - `reserved_amount` es el máximo visual para el cliente; el backend revalida el máximo real bajo lock. History no debe usarse para reconstruir reservas.
  - Validación local del gap: suite completa `663 passed, 9 skipped, 216 warnings`; PostgreSQL físico no fue ejecutado en esta auditoría.
  - Frontend Sync pausado hasta commit y despliegue del gap V1.8.

## Obligations Current State
- **Obligations Core:** Estabilizado operando sobre rutas `/api/v1.7`.
- **Características operativas:**
  - Gestión integral de períodos (Fixed y Variable).
  - Estados de lifecycle (pending, overdue, paid, skipped, cancelled).
  - Pagos parciales.
  - Asignación de pagos mediante FIFO (incluyendo períodos vencidos y slices idempotentes).
  - Smart one-button payment (delegación al backend para determinar distribución).
  - Generación automática de períodos tras acciones de lifecycle.
  - Batch auto-refresh.
  - Endpoint de Overview unificado y alineado con ORM.
  - Summary e Intelligence context.

## FX Current State
- **Endpoints:** Proveedor de snapshot de tasas mediante `/api/v1.7/fx/rates/latest`.
- **Snapshot Engine:**
  - Los snapshots (`rate_snapshot_id`) son persistidos y validables en base de datos.
  - El backend calcula el `source_amount` final con base en el snapshot.
  - El frontend actual (en desarrollo local) envía el `rate_snapshot_id`.
  - Soporte bidireccional de moneda: USD → COP y COP → USD (usando tasa inversa).
  - Operaciones `same-currency` asumen tasa 1 y no requieren snapshot.
  - DólarAPI es consumido *únicamente* desde el backend (o proveedor estático en desarrollo/testing).

## Important Compatibility Notes
- `quote_id` y `source_amount` todavía existen como campos opcionales en los esquemas por compatibilidad legacy.
- El flujo principal actual utiliza `rate_snapshot_id`. No obstante, `quote_id` no ha sido eliminado completamente del contrato.
- El preview legacy todavía existe y es soportado por el backend para clientes antiguos.
- El feature flag `NEXUM_OBLIGATIONS_V17_ENABLED` todavía existe como deuda técnica temporal protegiendo rutas V1.7 con `403`. No se deben crear nuevos flags de versión.
Updated for Wallets V1

- Integrado con [Wallets V1](./13-accounts-wallets.md) y [Frontend Sync](../../handoff/backend/wallets-v1-frontend-sync.md).
