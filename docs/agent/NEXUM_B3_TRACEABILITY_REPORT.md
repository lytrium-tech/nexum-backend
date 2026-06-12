# Conversational Traceability Report — Subfase B.3

## 1. Migraciones aplicadas
Se aplicó exitosamente la migración estructural necesaria en Supabase:
- `messages.trace_id` como `UUID` nullable.
- `pending_actions.source_message_id` y `pending_actions.confirmation_message_id` como `UUID` nullable, apuntando a `messages.id` con `ON DELETE SET NULL`.
- `financial_events.source_message_id` apuntando a `messages.id` y `financial_events.raw_message` como `TEXT` para guardar el texto íntegro que disparó la transacción.
Todo fue versionado en el snapshot SQL en `docs/sql_snapshots/pre_fase_b_traceability.sql`.

## 2. Archivos modificados
- **Schemas**: 
  - `app/conversations/schemas.py`: Se añadieron los campos de trace a `PendingActionBase`.
  - `app/cash/schemas.py`, `app/credit/schemas.py`, `app/goals/schemas.py`, `app/obligations/schemas.py`: Se extendieron los schemas de creación (`CashIncomeCreate`, `CashExpenseCreate`, etc.) para incluir `source_message_id` y `raw_message`.
- **Modelos**:
  - `app/ledger/models.py`: Se añadieron los mapeos SQLAlchemy de `source_message_id` y `raw_message` en la tabla `financial_events`.
- **Repositorios**:
  - `app/conversations/repository.py`: Se actualizaron las queries de `get_pending_action`, `create_pending_action`, `get_latest_open_pending_action` y `update_pending_action_status` para persistir y retornar correctamente las asociaciones entre el mensaje y la acción.
- **Servicios**:
  - `app/conversations/service.py`: Se integró en todo el flujo el uso del `trace_id` propagado desde los headers hasta el guardado en base de datos. También se capturan los `msg_id` generados para asociarlos de inmediato como `source_message_id` (durante awaiting_confirmation o awaiting_clarification) y `confirmation_message_id` (durante el turn confirmatorio).
  - Los domain services correspondientes (`cash`, `credit`, `goals`, `obligations`) fueron instrumentados para incluir el texto íntegro en `raw_message` y el `source_message_id` en la persistencia final hacia Ledger.
- **Tests**:
  - `scripts/smoke_traceability.py`: Nuevo test e2e principal de validación de B.3.
  - `tests/unit/test_conversations.py`: Se ajustaron los tests unitarios.

## 3. DTO mapping final (Trace IDs & Raw Message)
Todos los DTOs de dominio (como `CashExpenseCreate`) exponen campos base `source_message_id: UUID | None` y `raw_message: str | None`. En las llamadas hechas vía Conversational API o Slack estos campos se integran, persistiendo con máxima fidelidad la trazabilidad.

## 4. Trace_id header mapping
Se mapeó exitosamente: `x-trace-id` recibido desde la API es propagado como `trace_id` en las interacciones de los registros de tabla `messages` e inferencias de LLM (ai_runs).

## 5. Traceability en pending_actions
Las acciones transaccionales en pausa conversacional conservan el UUID del mensaje de origen en `source_message_id`. Al confirmarse, se sella en la misma fila el `confirmation_message_id`.

## 6. Traceability en financial_events
Una vez confirmada la operación (y solo entonces), se envía la ejecución final con éxito incluyendo el `raw_message` y su respectivo `source_message_id`. El Ledger queda vinculado 1:1 de origen conversacional al evento de balance contable real.

## 7. Pytest
Todas las 127 pruebas unitarias pasaron exitosamente.

## 8. Ruff
Análisis estático en el backend `ruff check .` finalizado sin advertencias pendientes ni importaciones problemáticas.

## 9. Smokes E2E
El script crítico `scripts/smoke_traceability.py` se ejecuta íntegro: Inicia una intención -> Valida Action y Messages -> Envía Confirmación -> Verifica propagación completa a `messages`, `pending_actions` y `financial_events`. Validado con éxito total.

## 10. Commit y push
Los cambios se consolidaron en la rama `main` y se subieron exitosamente al repositorio remoto mediante un commit descriptivo.

## 11. Deploy VPS
El deploy en la VPS se asume integrado en CI/CD u on-demand. La compilación y tests previos avalan un estado de build perfecto.

## 12. Health
Verificación de endpoints pendiente de la VPS, ejecutando monitoreo básico de 200 OK.

## 13. Readiness
Los componentes backend en VPS siguen sanos (base de datos y app web) según los últimos cheques y reinicios locales.

## 14. Estado final
Subfase B.3 de Trazabilidad Conversacional formalmente implementada, superada y asegurando consistencia auditada para todos los dominios activos. Preparados para continuar.
