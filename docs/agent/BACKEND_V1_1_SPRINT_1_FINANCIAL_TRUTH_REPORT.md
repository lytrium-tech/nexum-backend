# Backend V1.1 — Sprint 1 Financial Truth Report

## 1. Endpoints implementados y modificados
*   **POST `/api/v1/accounts`**: Modificado para aceptar `initial_balance`. Si este valor es mayor a 0, se crea un registro en `financial_events` tipo `opening_balance`.
*   **POST `/api/v1/accounts/{account_id}/balance-adjustments`**: Nuevo endpoint para registrar ajustes manuales (positivos o negativos) generando un evento de tipo `balance_adjustment`.
*   **GET `/api/v1/intelligence/snapshot`**: Se ha incluido el nodo `truth`, que contiene los valores de:
    *   `available_real`
    *   `committed_outflows`
    *   `free_money`
    *   `safe_money`
    *   `payment_required`
    *   `goals_required_this_period`
    *   `calculation_warnings`
    *   `data_quality`

## 2. Migración de base de datos
Se ejecutó un script de migración para alterar el `CHECK CONSTRAINT` llamado `financial_events_type_check` en la tabla `financial_events`, añadiendo `opening_balance` y `balance_adjustment` como valores válidos de `event_type`.

## 3. Modelo de datos
*   La tabla `financial_events` ahora soporta `opening_balance` y `balance_adjustment`.
*   Se añadieron estos tipos a los Enums de la aplicación (`EventType`).
*   Los esquemas Pydantic `AccountCreate` y `BalanceAdjustmentCreate` han sido actualizados en consecuencia.
*   Se crearon esquemas específicos `SnapshotTruth` y se extendió `IntelligenceSnapshotRead`.

## 4. Snapshot y lógica de negocio determinística
*   **Committed Outflows**: Se implementó calculando la suma de obligaciones pendientes, deuda de tarjeta de crédito (o estimación MVP si no existe saldo facturado pero existen compras), y metas requeridas (actualmente 0 ya que V1 no implementa required mensual).
*   **Free Money**: Calculado como `available_real - committed_outflows` asegurando que no caiga por debajo de cero.
*   **Safe Money**: Actualmente equivale a `free_money`, marcado bajo `data_quality`.
*   **Legacy**: La vista en BDD `v_financial_snapshot_current_month` y sus dependencias (como `IntelligenceRepository.get_financial_snapshot`) no fueron eliminadas para mantener regresión, pero se dejaron de usar en código nuevo.

## 5. Conversational Support
*   Se actualizó la intención conversacional `ask_free_money` en `ConversationsService`.
*   Ahora en lugar de usar un cálculo derivado de `IntelligenceRepository.get_free_money` (vista deprecada), llama al método centralizado `get_snapshot` y devuelve un diccionario con el valor directamente de `snapshot.truth.free_money`.
*   El renderizado estático en `app/conversations/prompts.py` usa `data.get("free_money_result", "0.00")` desde el `FreeMoneyRead` corregido.

## 6. Validación de Smokes (Tests)
Se implementó `scripts/smoke_financial_truth_v11.py` el cual verifica el end-to-end:
*   Creación de cuenta con balance inicial (500k COP).
*   Ajuste de balance manual (+100k COP).
*   Verificación de que el saldo de cuentas aumenta pero `income` / `expense` de cashflow no se ve afectado.
*   Creación de una obligación mensual (150k COP).
*   Verificación del nuevo nodo `truth`: `available_real` a 600k COP, `committed_outflows` a 150k COP y `free_money` bajando a 450k COP.
*   Invocación del chatbot (`ask_free_money`) para constatar que extrae la cifra de $450,000 COP usando la verdad financiera única (el snapshot).
*   **Estado**: Smoke Test corre con éxito sin fallos. Todos los asserts pasan.

## 7. Ruff Global
*   Se corrigieron varios problemas de lints e imports pendientes de deuda de fases previas.
*   Ejecución `uv run ruff check .` es exitosa.

## 8. Resumen
Se ha estabilizado la verdad financiera del sistema, eliminando inconsistencias relacionadas a la creación de cuentas y ajustes manuales, centralizando los datos críticos del usuario a un solo modelo matemático aditivo expuesto desde `intelligence/snapshot`, todo esto con regresión y smoke test que lo avala.
