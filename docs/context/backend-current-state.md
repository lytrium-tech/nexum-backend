# Backend Current State

## Repository State
- **Branch:** main
- **Repository HEAD:** `367ebdc`
- **OpenAPI:** Versionado y alineado con este estado.
- **Última suite verificada:** 293 passed, 3 skipped, 0 failed.
  *(Este valor corresponde a la última validación documental en el commit 367ebdc y puede cambiar con commits posteriores).*

## Repository vs Production
- **Repository Current State:** Reflejado en este documento y en `origin/main` (`367ebdc`).
- **Last Verified Production State:** La producción (VPS) no ha sido re-verificada durante esta fase documental. El último deploy conocido corresponde al commit `fcd47c0` (FX Rate Snapshot), pero `Production runtime was not re-verified during this documentation phase.`

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
