# Backend V1.2 Sprint 0 — Database Cleanup & Views Pruning Audit

## 1. Executive Summary
El Sprint 0 tiene como objetivo sanear la base de datos (eliminando basura generada por smokes, agentes y pruebas manuales) y podar vistas obsoletas o de diagnóstico (legacy v6) antes de implementar las lógicas funcionales de Backend V1.2. Se ha realizado un inventario completo de 19 tablas y 15 vistas mediante consultas seguras para planificar un borrado estructurado y ordenado que garantice la integridad referencial de los datos y no rompa la aplicación.

## 2. Database Inventory
Se inspeccionaron las tablas base de `public`:
- **Users & Channels**: `users`, `user_channels`
- **Core Entities**: `accounts`, `categories`, `credit_cards`, `goals`, `obligations`
- **Ledger & Transactions**: `financial_events`, `credit_card_transactions`, `credit_card_installments`, `goal_contributions`, `obligation_payments`, `transfers`, `transactions_legacy_backup`
- **Agent & AI State**: `messages`, `ai_runs`, `logs`, `pending_actions`, `idempotency_keys`

## 3. Row Counts
- `financial_events`: 316
- `messages`: 242
- `users`: 236
- `accounts`: 214
- `credit_card_installments`: 144
- `credit_card_transactions`: 120
- `categories`: 109
- `credit_cards`: 79
- `ai_runs`: 73
- `goals`: 65
- `obligations`: 56
- `pending_actions`: 44
- `goal_contributions`: 37
- `obligation_payments`: 14
- `transfers`: 13
- `user_channels`: 0
- `transactions_legacy_backup`: 0
- `logs`: 0
- `idempotency_keys`: 0

## 4. Data Cleanup Candidates
Todas las tablas contienen altos volúmenes de datos derivados de pruebas masivas (ej. 236 usuarios en entorno de desarrollo).
- **Candidates for Full Cleanup**: Tablas puramente transaccionales y de logs (`financial_events`, `messages`, `ai_runs`, `credit_card_transactions`, etc.).
- **Candidates for Partial Cleanup**: Tablas estructurales (`users`, `accounts`, `categories`). Si se desea un borrado total, se deberá aplicar un reseed posterior del usuario dev (`DEV_USER_ID = 00000000-0000-0000-0000-000000000001`).

## 5. Cleanup Dependency Order
Para respetar los Foreign Keys sin usar truncados destructivos en cascada impredecibles, el orden seguro de eliminación (DELETE) es estricto de hijo a padre:
1. `logs`, `ai_runs`, `messages`, `pending_actions`, `idempotency_keys`
2. `credit_card_installments`, `credit_card_transactions`
3. `goal_contributions`, `obligation_payments`, `transfers`, `transactions_legacy_backup`
4. `financial_events`
5. `credit_cards`, `obligations`, `goals`
6. `accounts`, `categories`
7. `user_channels`, `users`

## 6. Backup Strategy
Antes de ejecutar cualquier sentencia, se requiere generar un backup lógico o snapshot de la instancia en Supabase.
**Acción:** Dashboard Supabase -> Database -> Backups -> Create Manual Snapshot.

## 7. Proposed Data Cleanup SQL
```sql
BEGIN;

-- 1. Agent & Logs
DELETE FROM public.logs;
DELETE FROM public.ai_runs;
DELETE FROM public.messages;
DELETE FROM public.pending_actions;
DELETE FROM public.idempotency_keys;

-- 2. Transaction Details
DELETE FROM public.credit_card_installments;
DELETE FROM public.credit_card_transactions;
DELETE FROM public.goal_contributions;
DELETE FROM public.obligation_payments;
DELETE FROM public.transfers;
DELETE FROM public.transactions_legacy_backup;

-- 3. Core Ledger
DELETE FROM public.financial_events;

-- 4. Financial Entities
DELETE FROM public.credit_cards;
DELETE FROM public.obligations;
DELETE FROM public.goals;
DELETE FROM public.accounts;
DELETE FROM public.categories;

-- 5. Users (Opcional: vaciar todo)
DELETE FROM public.user_channels;
DELETE FROM public.users;

COMMIT;
```

## 8. Views Inventory
Vistas detectadas en el esquema `public`:
1. `v_credit_card_debt`
2. `v_cashflow_current_month`
3. `v_consumption_current_month`
4. `v6_view_cols`
5. `v_pending_obligations_current_month`
6. `v_wealth_velocity_current_month`
7. `v6_credit_card_status`
8. `v_consumption_summary_current_month`
9. `v6_intel_views_check`
10. `v6_functions_list`
11. `v6_indices_check`
12. `v_account_balances`
13. `v_financial_snapshot_current_month`
14. `v6_functions_args`
15. `v_goals_current_month`

## 9. Views Usage Analysis
- **Activamente referenciadas por el código backend** (en `app/intelligence/repository.py` y `app/credit/repository.py`):
  - `v_financial_snapshot_current_month`
  - `v_cashflow_current_month`
  - `v_consumption_summary_current_month`
  - `v_account_balances`
  - `v_credit_card_debt`
  - `v_goals_current_month`
  - `v_pending_obligations_current_month`
- **Legacy / Diagnóstico (v6)**: Todas las vistas que inician con `v6_` fueron creadas para auditorías temporales de migraciones pasadas. No tienen referencias en el código activo.
- **Obsoletas / Sin uso activo**: `v_wealth_velocity_current_month`, `v_consumption_current_month`. Sólo aparecen en archivos de la carpeta `docs/archive` o históricos.

## 10. Views Classification
**KEEP**:
- `v_credit_card_debt`
- `v_financial_snapshot_current_month`
- `v_cashflow_current_month`
- `v_consumption_summary_current_month`
- `v_account_balances`
- `v_goals_current_month`
- `v_pending_obligations_current_month`

**DROP_CANDIDATE**:
- `v6_view_cols`
- `v6_credit_card_status`
- `v6_intel_views_check`
- `v6_functions_list`
- `v6_indices_check`
- `v6_functions_args`
- `v_wealth_velocity_current_month`
- `v_consumption_current_month`

## 11. Proposed Views Pruning SQL
```sql
BEGIN;

DROP VIEW IF EXISTS public.v6_view_cols;
DROP VIEW IF EXISTS public.v6_credit_card_status;
DROP VIEW IF EXISTS public.v6_intel_views_check;
DROP VIEW IF EXISTS public.v6_functions_list;
DROP VIEW IF EXISTS public.v6_indices_check;
DROP VIEW IF EXISTS public.v6_functions_args;
DROP VIEW IF EXISTS public.v_wealth_velocity_current_month;
DROP VIEW IF EXISTS public.v_consumption_current_month;

COMMIT;
```

## 12. Risks
- Si se limpia la tabla `users` completamente, las pruebas locales de entorno pueden fallar por falta del `DEV_USER_ID` hasta que se ejecute el reseed.
- Eliminar las vistas `v6_` removerá atajos de auditoría en la base de datos, lo cual es intencionado, pero requerirá consultar `information_schema` o `pg_stat` manualmente a futuro.

## 13. Verification Plan
1. Ejecutar unit tests tras la limpieza (deberían pasar si usan fixtures/factories, o fallar si asumen estado persistido).
2. Ejecutar un Smoke Test completo (`python -m scripts.smoke.smoke_financial_events` u otros disponibles) para confirmar que el backend arranca y la base recibe data sin problemas.
3. Verificar el log del backend al arrancar que no existan errores de vistas faltantes en repositorios.

## 14. Reseed Plan
Luego de limpiar la base, si se borraron los usuarios, se requerirá poblar los elementos mínimos:
1. Crear el usuario `00000000-0000-0000-0000-000000000001` (Dev User).
2. Insertar categorías estándar necesarias.
Se aconseja el uso de un script local (ej. `scripts/dev/reseed_db.py`) o reconfiguración inicial mediante registro local en un test controlado.

## 15. Approval Required
El Sprint 0 está listo para ejecución técnica, sujeto a:
1. Aprobación del SQL de limpieza de Data.
2. Aprobación de la política de `users` (Excluir DEV vs. Borrar Todo).
3. Aprobación del SQL de poda de Vistas (`DROP_CANDIDATE`).

## 16. Final Recommendation
Se recomienda realizar la limpieza y poda conjuntas en una ventana controlada, seguido inmediatamente del "Reseed" del usuario maestro de pruebas para liberar una base estéril hacia los desarrollos funcionales del Sprint 1. El plan está formalmente listo para aprobación.
