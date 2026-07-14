# Accounts / Wallets V1

## Purpose
- Billeteras como representación de cuentas financieras reales o virtuales.
- Backend como fuente de verdad para balance y eventos financieros.

## Account Model
- El modelo expone campos reales que el usuario configura: `name` (presentación), `currency` (moneda), `balance` (saldo), `type` (tipo de cuenta), `is_active` (estado lógico), y `user_id` (ownership).

## Create
- Endpoint: `POST /api/v1/accounts`
- Payload: `AccountCreate`
- Reglas: Nombre normalizado único por usuario, balance inicial inyecta evento de `OPENING_BALANCE` si es mayor a 0, moneda inmutable tras creación.

## Edit
- Endpoint: `PATCH /api/v1/accounts/{account_id}`
- Campos editables: `name`, `type`.
- Campos inmutables: `balance` (solo por balance adjustment), `currency` (por seguridad Ledger), `is_active` (solo vía Archive/Restore).

## Archive and Restore
- Endpoints: `POST /api/v1/accounts/{account_id}/archive` y `POST /api/v1/accounts/{account_id}/restore`.
- Idempotencia garantizada por re-asignación del estado base.
- Cuenta archivada bloqueada de ser usada como origen en operaciones nuevas (excluida vía consultas directas con `include_inactive=False` por default).
- Lectura histórica preservada para cuentas archivadas (mediante explicitud de flags o subconsultas históricas).

## Account Detail
- Endpoint: `GET /api/v1/accounts/{account_id}`
- Expone esquema extendido `AccountDetailRead`.
- Campos generados al vuelo desde Ledger (limit=1): `has_movements`, `movement_count`, `last_movement_at`.

## Movements
- Endpoint: `GET /api/v1/accounts/{account_id}/movements`
- Fuente única de historial: Ledger (`financial_events`).
- Resuelve paginación, filtros temporales, event_type y dirección.
- Transacciones cross-account o cross-currency respetan la perspectiva real del usuario, reportando siempre la `direction` correcta localmente (las transferencias son registros duales `TRANSFER_IN` y `TRANSFER_OUT` sobre las cuentas involucradas independientemente).
- Provee un empty response estructurado ante ausencia de historial.

## Monthly Summary
- Endpoint: `GET /api/v1/accounts/{account_id}/summary`
- Agrupación por tipo y dirección en un rango delimitado por YYYY-MM.
- Calcula explícitamente:
  - `total_inflows`: ingresos operativos
  - `total_outflows`: egresos operativos
  - `transfer_inflows`: transferencias entrantes
  - `transfer_outflows`: transferencias salientes
  - `operating_net_flow`: `total_inflows - total_outflows` (no incluye transferencias)
- Límite inclusivo en inicio y exclusivo hacia fin de mes.

## Balance Adjustment
- Endpoint: `POST /api/v1/accounts/{account_id}/adjustments`
- Payload: `target_balance` (Decimal, >=0), `reason`, `idempotency_key`.
- Atomicidad garantizada: uso de row-lock concurrencial (`SELECT FOR UPDATE`), pre-validación de diff.
- Transaccionalidad (UoW): cálculo de delta in-memory `delta = target_balance - current_balance` resultando en un ingreso manual (`manual_adjustment` / `inflow`) o egreso (`outflow`).
- Idempotencia delegada al `command_id` en PostgreSQL.
- Balance jamás editado de forma manual (REST direct).

## Delete Policy
- `DELETE /api/v1/accounts/{account_id}` está deprecado.
- Archive (is_active=False) es la única vía segura en V1.
- No hay hard delete habilitado ni soft delete simulado en GET accounts sin explicitar `include_archived=True`.

## Error Contracts
- `AccountDuplicateError` (409) - Nombre duplicado.
- `AccountForbiddenError` (403) - Intento cruzado de propiedad de datos.
- Error 422 con detalle `"account_deletion_not_supported_use_archive"` al invocar DELETE.

## Frontend Rules
- Backend calcula saldos y movimientos, Frontend representa.
- Frontend implementa vistas vacías, de carga y gestión de errores correspondientes.
- Frontend respeta la inmutabilidad: bloquea u oculta form inputs para campos bloqueados.
- Archive/Restore representados como acciones primarias sobre una cuenta, relegando DELETE a visuales nulas o disabled.

## V2 Deferred
- Eliminación segura y real (si aplican purgas completas de metadata).
- Manejo avanzado de cuotas u otras reglas diferidas.

## Last Verified Against
- Commit baseline 502717b
