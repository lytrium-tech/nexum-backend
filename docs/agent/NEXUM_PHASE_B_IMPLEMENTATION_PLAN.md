# Nexum Backend MVP V2 — Fase B Planning (Actualizado)

Este documento contiene el plan técnico corregido y exacto para abordar la Fase B (Conversational Execution Reliability & Traceability), validado contra la base de datos y código fuente actuales.

## 1. DTOs Corregidos y Mapping Exacto
Al mapear el intent del usuario (ej. detectado por NLU) a la ejecución, se utilizarán exactamente los schemas Pydantic definidos en los dominios:

| Intent NLU | DTO Destino | Campos Exactos |
|---|---|---|
| `create_income` | `CashIncomeCreate` | `account_id` (UUID, req), `amount` (Decimal, req), `category_id` (UUID, opc), `description` (str, opc) |
| `create_expense`| `CashExpenseCreate` | `account_id` (UUID, req), `amount` (Decimal, req), `category_id` (UUID, opc), `description` (str, opc) |
| `create_goal`   | `GoalCreate` | `name` (str, req), `target_amount` (Decimal, req), `target_date` (date, opc) |
| `create_goal_contribution` | `GoalContributionCreate` | `account_id` (UUID, req), `amount` (Decimal, req) |
| `create_obligation` | `ObligationCreate` | `name` (str, req), `amount` (Decimal, req), `due_day` (int, opc), `frequency` (str, opc), `category_id` (UUID, opc), `metadata` (dict, req - default `{}`) |
| `create_obligation_payment`| `ObligationPaymentCreate` | `account_id` (UUID, req), `amount` (Decimal, req) |
| `create_credit_card_purchase` | `CreditCardPurchaseCreate` | `amount` (Decimal, req), `category_id` (UUID, opc), `description` (str, opc), `installments_total` (int, req) |
| `create_credit_card_payment` | `CreditCardPaymentCreate` | `account_id` (UUID, req), `amount` (Decimal, req) |

*(Nota: En todas las operaciones originadas por chat, los campos requeridos faltantes dispararán el Clarification Flow).*

## 2. Ledger Enums Reales y Ejecución
Los Enum reales verificados en la base de datos para `financial_events` son:
**EventType:** `income`, `expense`, `goal_contribution`, `obligation_payment`, `credit_card_purchase`, `credit_card_payment`.
**Direction:** `inflow`, `outflow`, `neutral`.

| Intent | Event Type | Direction | Tabla Auxiliar afectada |
|---|---|---|---|
| `create_income` | `income` | `inflow` | N/A (Actualiza balance cuenta) |
| `create_expense` | `expense` | `outflow` | N/A (Actualiza balance cuenta) |
| `create_goal_contribution` | `goal_contribution` | `outflow` | Inserta en `goal_contributions` |
| `create_obligation_payment`| `obligation_payment` | `outflow` | Inserta en `obligation_payments` |
| `create_credit_card_purchase` | `credit_card_purchase` | `neutral` | Inserta en `credit_card_transactions` |

## 3. Credit Payment Corregido
**Flujo real verificado en `CreditCardService.create_payment`:**
Un pago de tarjeta de crédito **no genera dos eventos de Ledger**. Al crearse mediante `create_credit_card_payment`:
1. Crea un **único evento de Ledger**: `event_type = 'credit_card_payment'`, `direction = 'outflow'` que reduce el saldo de la cuenta bancaria origen.
2. Crea un registro en la tabla auxiliar **`credit_card_transactions`** (`type = 'payment'`), lo que computa para reducir el estimado de la deuda actual.
*Conclusión:* Es seguro conversacionalmente mapear directo a este handler sin violar índices `UNIQUE(command_id)` del Ledger.

## 4. Clarification Intent Real
El NLU (Gemini) devuelve `clarify_action` en su schema actual. **No se creará `clarify_entity`**. 
En la máquina de estados, cuando el usuario clarifica una acción pendiente, y el NLU arroja `clarify_action`, el sistema realizará un merge seguro.
**Clarification Reset:** Si el sistema está en `awaiting_clarification` y el usuario envía un mensaje que detona un intent de mutación distinto (ej. estaba clarificando un gasto y repentinamente dice *"Crea la meta Ahorro Coche por 200 mil"*), el sistema **cancelará** la Pending Action anterior de forma automática, iniciará la nueva, y notificará sutilmente al usuario (ej. *"Cancelé tu registro anterior. Voy a crear la meta..."*). En caso de ambigüedad severa, preguntará en vez de cancelar ciegamente.

## 5. Sistema de Migraciones Confirmado
Nexum no utiliza Alembic. Todo el versionado de base de datos se realiza **manualmente mediante scripts SQL guardados en `docs/sql_snapshots/`**. 
En la Fase B, toda alteración de tablas generará un archivo `docs/sql_snapshots/pre_fase_b_traceability.sql`.

## 6. Transactions Rename Plan (B.0)
Decisión actualizada para retirar `public.transactions` (n8n legacy):
Se creará un script SQL ejecutado manualmente en Supabase:
```sql
ALTER TABLE public.transactions RENAME TO transactions_legacy_backup;
COMMENT ON TABLE public.transactions_legacy_backup IS 'Legacy backup of public.transactions before retirement. Official ledger is public.financial_events.';
```
*Rollback si hay catástrofe:* `ALTER TABLE public.transactions_legacy_backup RENAME TO transactions;`

Los asserts en `backend/scripts/` (como `smoke_cash`, `cleanup_dev`) serán actualizados para **validar exactamente el número de `financial_events` o sus campos** en vez de usar condicionales ciegos que fallan.

## 7. Ownership Audit Ampliado
Dado que la conexión de backend bypassea RLS (actuando como root/postgres), todos los repositorios deben incluir `user_id = user_id` en sus `SELECT`, `UPDATE` y `DELETE`.
Auditoría requerida en Fase B.5 para cada repositorio:
- `AccountsRepository`
- `CategoriesRepository`
- `CashRepository`
- `GoalsRepository`
- `ObligationsRepository`
- `CreditRepository`
- `ConversationsRepository`
- `LedgerRepository`

**Tests de regresión cruzada a añadir:** `test_user_a_cannot_modify_user_b_resource` para todos estos dominios.

## 8. Subfases Actualizadas
### B.0 — Retiro reversible de transactions legacy
- **Archivos:** Crear `docs/sql_snapshots/pre_transactions_legacy_rename.sql`. Modificar smokes.
- **Criterio Aceptación:** `public.transactions` no existe y los smokes pasan leyendo `financial_events`.
- **Commit:** `chore: rename legacy transactions and fix smoke assertions`

### B.1 — Fail-closed
- **Archivos:** `exceptions.py`, `service.py`.
- **Criterio Aceptación:** Lanzar `UnsupportedConversationalIntentError` e interrumpir la ejecución sin responder "éxito" para los intents no soportados actualmente en `_execute_financial_action`.

### B.2 — Execution handlers faltantes
- **Archivos:** `service.py`.
- **Criterio Aceptación:** Llenar los branch de ejecución para `create_goal`, `create_goal_contribution`, `create_obligation`, `create_obligation_payment`, `create_credit_card_payment`.

### B.3 — Traceability
- **Migración:** `ALTER TABLE pending_actions ADD COLUMN source_message_id UUID`, etc.
- **Criterio Aceptación:** Enlazar el flujo `Mensaje -> Pending Action -> Confirmación -> Financial Event`. (API sin mensaje usa `NULL`).

### B.4 — Clarification multi-turn
- **Archivos:** `service.py`.
- **Criterio Aceptación:** Permitir que el estado `awaiting_clarification` integre información faltante, o haga reset automátco si el intent cambia bruscamente.

### B.5 — Ownership hardening
- **Archivos:** Repositorios de todos los dominios y test suite de seguridad.
- **Criterio Aceptación:** Garantizar la inclusión de cláusulas `user_id == user_id` en todos los mutadores ORM dado el RLS bypass existente.

### B.6 — Regresión completa + deploy
- Pruebas exhaustivas unitarias y E2E antes de hacer commit a `main` y deploy al VPS.

## 9. Preguntas Restantes
Ninguna pregunta pendiente del lado técnico. Estoy listo para recibir tu comando e iniciar la implementación paso a paso por subfases (iniciando por B.0).
