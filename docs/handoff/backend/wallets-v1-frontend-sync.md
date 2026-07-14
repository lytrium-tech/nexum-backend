# Wallets V1 Frontend Sync Contracts

Este documento expone los contratos precisos y los comportamientos esperados desde el lado del Backend (Wallets V1) para su implementación correcta en el Frontend.

## GET `/api/v1/accounts`
- **Descripción:** Lista cuentas del usuario.
- **Payload:** N/A (query `include_archived=false` default)
- **Response:** Lista de `AccountRead`
- **Errores:** 401 Unauthorized
- **Empty State:** `[]` - Representar prompt de creación inicial.

## POST `/api/v1/accounts`
- **Descripción:** Crea cuenta.
- **Payload:** `AccountCreate` (name, type, currency, initial_balance)
- **Response:** `AccountRead`
- **Errores:** 409 `AccountDuplicateError`, 422 ValidationError.
- **Reglas Financieras:** initial_balance > 0 despacha evento silencioso de `OPENING_BALANCE`.

## GET `/api/v1/accounts/{account_id}`
- **Descripción:** Detalle de la cuenta.
- **Payload:** N/A
- **Response:** `AccountDetailRead` (extiende AccountRead con `has_movements`, `movement_count`, `last_movement_at`)
- **Errores:** 404 NotFound, 403 Forbidden.
- **Reglas Financieras:** Retorna la información sin importar si está archivada o activa.

## PATCH `/api/v1/accounts/{account_id}`
- **Descripción:** Edita detalles descriptivos.
- **Payload:** `AccountUpdate` (acepta únicamente `name`, `type`)
- **Response:** `AccountRead`
- **Errores:** 409 Duplicate, 422 ValidationError (si intenta enviar `is_active`, `balance`, `currency` o campos extra).
- **Reglas Financieras:** Moneda y balance no son editables por esta vía. El estado activo/inactivo está bloqueado.

## POST `/api/v1/accounts/{account_id}/archive`
- **Descripción:** Archiva una cuenta (soft delete operativo).
- **Payload:** N/A
- **Response:** `AccountRead` con `is_active=False`
- **Errores:** 404 NotFound.
- **Archive State:** Frontend debe inhabilitar account en selectores/dropdowns (ej: origen de pago) si está archivada.

## POST `/api/v1/accounts/{account_id}/restore`
- **Descripción:** Restaura una cuenta archivada.
- **Payload:** N/A
- **Response:** `AccountRead` con `is_active=True`
- **Errores:** 404 NotFound.

## GET `/api/v1/accounts/{account_id}/movements`
- **Descripción:** Pagina los movimientos de la cuenta desde el Ledger.
- **Query Params:** limit, offset, date_from, date_to, event_type, direction.
- **Response:** `LedgerEventsResponse` (con items y metadata pagination).
- **Empty State:** `items: []`.
- **Reglas Financieras:** Transferencias cruzadas reportan el flujo correcto para esta cuenta específicamente.

## GET `/api/v1/accounts/{account_id}/summary`
- **Descripción:** Resumen operativo de un mes específico.
- **Query Params:** `month` (formato `YYYY-MM`).
- **Response:** `AccountPeriodSummary` (incluye `total_inflows`, `total_outflows`, `transfer_inflows`, `transfer_outflows`, `operating_net_flow`).
- **Reglas Financieras:** `operating_net_flow` excluye estrictamente las transferencias cruzadas, operando únicamente con ingresos/gastos.

## POST `/api/v1/accounts/{account_id}/adjustments`
- **Descripción:** Corrige o concilia el saldo final.
- **Payload:** `BalanceAdjustmentCreate` (target_balance, reason, idempotency_key).
- **Response:** `AccountRead`
- **Errores:** 422 si `target_balance < 0`.
- **Reglas Financieras:** Backend computa el delta y lo impacta atómicamente. target_balance denota saldo final absoluto.

## DELETE `/api/v1/accounts/{account_id}` (DEPRECATED)
- **Descripción:** Intento de hard-delete.
- **Response:** Lanza estrictamente HTTP 422 con detalle `"account_deletion_not_supported_use_archive"`.
- **Reglas Financieras:** Hard deletes prohibidos en V1. Frontend debe usar Archive e inhabilitar/ocultar botones de Delete visualmente.
