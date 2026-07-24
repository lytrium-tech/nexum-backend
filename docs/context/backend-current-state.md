# Backend Current State

## Repository State
- **Checkout actual:** rama `goals-v1-phase2-releases`
- **Repository HEAD:** `c26de44f81b3446d41ea8b49a08d6f73d6c114ad`
- **origin/main:** `c26de44f81b3446d41ea8b49a08d6f73d6c114ad`
- **main local:** `61cfc3822730e6f59e3097d3f9f86f24b68350d4`
- **Cambios locales:** Metas V1 Fase 2, Bloques 1–3, permanece local y sin commit.

## Repository vs Production
- **Repository Current State:** `HEAD` y `origin/main` apuntan a `c26de44f81b3446d41ea8b49a08d6f73d6c114ad`; `main` local todavía apunta a `61cfc3822730e6f59e3097d3f9f86f24b68350d4`.
- **Production:** Metas V1 Fase 1 está desplegada.
- **Migración productiva de Metas:** `goals_v1_ph1_reserved`.
- **Metas V1 Fase 2:** no está desplegada.

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
- **Goals V1 Phase 2 (local, sin commit):**
  - Migración propuesta: `goals_v1_ph2_releases`.
  - Bloque 1 (Fundación): Gate PostgreSQL aprobado para upgrade, constraint canónico, repository y bloqueo de downgrade con eventos `goal_release`. Constraint canónico actualizado.
  - Bloque 2 (Motor): Servicio core `create_release` con locks jerárquicos y control de idempotencia nativo. Cobertura completa de tests en core.
  - Bloque 3 (API e Integración): Router implementado, compatibilidad History/Availability/Intelligence.
  - Bloque 4 (Release Gate): Ejecutado exitosamente en Argos local. Cero drift. Migración aprobada estructural y funcionalmente. Base de datos Gate restaurada a `goals_v1_ph1_reserved`.
  - Fase 2 lista para revisión, commit y despliegue a producción.
  - Fase 2 no tiene commit y no está desplegada.

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
