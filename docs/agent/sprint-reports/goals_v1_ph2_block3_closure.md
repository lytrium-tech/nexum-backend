# Reporte de implementación local: Metas V1 Fase 2 — Bloque 3

## Resumen Ejecutivo
El Bloque 3 expone localmente el motor de releases desarrollado en los Bloques 1 y 2 mediante HTTP e integra sus contratos con History, Availability e Intelligence. Fase 2 no tiene commit y no está desplegada.

## Alcance Completado

1. **API HTTP:**
   - Se expuso el endpoint `POST /api/v1/goals/{goal_id}/releases` en `app/goals/router.py`.
   - Se implementaron los esquemas de entrada y respuesta en `app/goals/schemas.py` (`GoalReleaseCreate`, `GoalReleaseResult`).
   - El endpoint delega en `GoalService.create_release`; `get_db_session` conserva la única frontera de commit/rollback de la request y el servicio usa savepoints para las escrituras del release.
   - El contrato documenta 400 para estados inválidos de meta, 403 para cuenta inactiva o sin saldo, 404 anti-enumeración para cuenta/meta inexistente o ajena, 409 para conflictos, 422 para payload inválido y 401 para autenticación.

2. **History (GoalTransactions):**
   - El endpoint preexistente `GET /api/v1/goals/{goal_id}/transactions` fue validado de forma nativa para soportar eventos `release`. Dado que los `GoalTransaction` insertados usan sumas aritméticas absolutas (`applied_amount > 0`) pero indican una reducción lógica (`transaction_type = release`), el cliente puede parsear eficientemente las devoluciones y depósitos pasados.
   - La respuesta del modelo en la API (p. ej. `GoalTransactionRead`) soporta intrínsecamente la enumeración de `GoalTransactionType.release`.

3. **Availability (Accounts):**
   - La lógica de disponibilidad de cuentas (`get_account_availability`) fue validada contra las operaciones de `release`.
   - Se confirmó que al reducir `goal_account_reserved_amount` dentro de `GoalService`, el cálculo dinámico de `AccountAvailabilityRead` detecta pasivamente la reducción del total de reservas, aumentando así dinámicamente el `available_balance` sin necesidad de alterar el balance bruto de la cuenta (`account.balance`), cumpliendo el axioma financiero número uno.

4. **Intelligence:**
   - Las consultas de cashflow usan listas cerradas de `income`, `expense`, pagos y `goal_contribution` legacy con `direction = 'outflow'`; `goal_release` no entra en income, expense ni cashflow operativo.
   - Para metas con fecha vigente, la reducción de reserva y el aumento de `remaining_required_this_period` se compensan, manteniendo estable el free money planificado.

5. **Pruebas locales:**
   - `tests/unit/test_goals_v1_phase2_block3.py` cubre delegación HTTP, serialización completa, códigos 400/401/403/404/409/422, header idempotente, ausencia de doble UoW, History sin metadata privada y exclusión de `goal_release` de cashflow.
   - Las pruebas HTTP sustituyen el servicio; las garantías de locks, atomicidad y concurrencia pertenecen al Gate funcional/Release Gate.
   - Suite completa local: `651 passed, 9 skipped, 216 warnings`.

6. **OpenAPI:**
   - `openapi.json` se genera con `scripts/dev/export_openapi.py`.
   - El contrato expone `POST /api/v1/goals/{goal_id}/releases`, sus esquemas, el header `Idempotency-Key` y los códigos HTTP efectivos.

## Estado de cierre
- Bloques 1–3: implementados localmente.
- Gate funcional de Bloque 2: reportado como aprobado.
- Auditoría final consolidada local: aprobada; Release Gate de Fase 2: pendiente.
- Sin commit, push, conexión a producción, VPS ni despliegue.
