# Backend V1.1 Sprint 6 — Frontend Alignment Scope

## 1. Executive Summary
Sprint 6 focuses on aligning the Backend API contracts with Frontend expectations, enforcing the core rule: "Backend calcula. Frontend representa. LLM explica." This audit identifies gaps where the backend fails to provide calculated fields in base schemas, forcing the frontend to compute financial truth.

## 2. Current Backend Contracts
The backend exposes base CRUD models (e.g., `GoalRead`, `ObligationRead`) and aggregated intelligence models (e.g., `IntelligenceSnapshotRead`). While intelligence endpoints provide rich calculations, base entity endpoints often lack period-specific or dynamically calculated fields.

## 3. OpenAPI Findings
A review of `openapi.json` schemas reveals inconsistencies primarily in Obligations and Credit Cards, where period status and debt breakdowns are missing from the base read models. Goals are mostly aligned but missing one period-specific calculation.

## 4. Accounts Contract
- **campos backend actuales:** `id`, `name`, `type`, `balance`, `currency`, `is_active`
- **campos que debe consumir frontend:** Todos los anteriores, confiando exclusivamente en `balance`.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** Ninguno.
- **riesgos de romper compatibilidad:** Ninguno.
- **cambios backend requeridos:** Ninguno.
- **cambios frontend requeridos:** Asegurar que no hay recálculos de balance locales.

## 5. Categories Contract
- **campos backend actuales:** `id`, `name`, `type`, `is_active`, `is_global`
- **campos que debe consumir frontend:** Todos los anteriores.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** Ninguno.
- **riesgos de romper compatibilidad:** Ninguno.
- **cambios backend requeridos:** Ninguno.
- **cambios frontend requeridos:** Ninguno.

## 6. Goals Contract
- **campos backend actuales:** `id`, `target_amount`, `current_amount`, `remaining_amount`, `progress_percentage`, `monthly_required`, `daily_required`
- **campos que debe consumir frontend:** Todos los anteriores + `remaining_required_this_period`.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** `remaining_required_this_period` existe en `IntelligenceGoalRead` pero falta en `GoalRead`.
- **riesgos de romper compatibilidad:** Bajo.
- **cambios backend requeridos:** Agregar `remaining_required_this_period` a `GoalRead`.
- **cambios frontend requeridos:** Consumir este campo en lugar de calcularlo en el cliente.

## 7. Obligations Contract
- **campos backend actuales:** `id`, `amount`, `due_day`, `frequency`, `is_active`
- **campos que debe consumir frontend:** Los anteriores + estado del periodo actual.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** Faltan `remaining_amount`, `period_status` y `is_pending` en `ObligationRead`.
- **riesgos de romper compatibilidad:** Bajo.
- **cambios backend requeridos:** Exponer `remaining_amount`, `period_status`, `is_pending` en `ObligationRead`.
- **cambios frontend requeridos:** Eliminar lógica de cálculo de fechas y montos pendientes; usar solo flags del backend.

## 8. Credit Contract
- **campos backend actuales:** `CreditCardRead` expone `estimated_current_debt`, `monthly_cc_payment`, `estimated_available_credit`.
- **campos que debe consumir frontend:** Desglose real (`total_debt`, `billed_debt`, `unbilled_debt`).
- **campos legacy/deprecated:** `estimated_current_debt` y `estimated_available_credit` son ambiguos comparados con `CreditCardStatusRead`.
- **gaps del contrato:** `CreditCardRead` carece del desglose exacto de deuda.
- **riesgos de romper compatibilidad:** Medio (si el frontend ya depende de `estimated_current_debt`).
- **cambios backend requeridos:** Unificar `CreditCardRead` para incluir `total_debt`, `billed_debt` y `unbilled_debt`.
- **cambios frontend requeridos:** Migrar a las nuevas propiedades de desglose de deuda.

## 9. Snapshot/Home Contract
- **campos backend actuales:** `IntelligenceSnapshotRead` con agrupaciones `cash`, `cashflow`, `debt`, `goals`, `obligations`, `transfers`.
- **campos que debe consumir frontend:** La vista completa del Home.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** El bloque `free_money` no está incluido en el Snapshot (requiere llamada separada o cálculo frontend).
- **riesgos de romper compatibilidad:** Bajo.
- **cambios backend requeridos:** Evaluar incluir `free_money` dentro de `IntelligenceSnapshotRead`.
- **cambios frontend requeridos:** Consumir todo el estado inicial desde un solo endpoint.

## 10. Ledger/History Contract
- **campos backend actuales:** `LedgerTimelineGroup`, `LedgerEventDetail`.
- **campos que debe consumir frontend:** Agrupaciones temporales y detalles de eventos.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** Ninguno crítico.
- **riesgos de romper compatibilidad:** Ninguno.
- **cambios backend requeridos:** Ninguno.
- **cambios frontend requeridos:** Renderizar basado en los grupos devueltos.

## 11. Conversations Contract
- **campos backend actuales:** `ConversationalRequest`, `ConversationalResponse`.
- **campos que debe consumir frontend:** `response_text`, `structured_data`.
- **campos legacy/deprecated:** Ninguno.
- **gaps del contrato:** No hay un esquema claro de `ConversationMessageRead` para recuperar el historial de chat; solo endpoint POST.
- **riesgos de romper compatibilidad:** Bajo.
- **cambios backend requeridos:** Si el frontend requiere cargar historial, se necesita un endpoint `GET /conversations/{id}/messages`.
- **cambios frontend requeridos:** Consumir historial desde el backend en lugar de persistencia local.

## 12. Legacy Aliases
- `estimated_current_debt` vs `total_debt` en Credit Cards.
- Inconsistencia entre esquemas "Read" base y "IntelligenceRead".

## 13. Backend Changes Required
1. **Goals**: Añadir `remaining_required_this_period` a `GoalRead`.
2. **Obligations**: Añadir `remaining_amount`, `period_status`, `is_pending` a `ObligationRead`.
3. **Credit**: Unificar propiedades de `CreditCardRead` con `CreditCardStatusRead`.
4. **Snapshot**: (Opcional) Incorporar `free_money` a `IntelligenceSnapshotRead`.
5. **Conversations**: (Opcional) Añadir endpoints de historial si es necesario.

## 14. Frontend Changes Required
1. **Delegación total**: Eliminar toda lógica de cálculo de estados de periodo (metas, obligaciones).
2. **Migración de campos**: Apuntar a `total_debt` / `billed_debt` en lugar de `estimated_current_debt`.
3. **Optimización**: Consumir el Snapshot de manera integral.

## 15. Risks
- Renombrar `estimated_current_debt` a `total_debt` en `CreditCardRead` es un *breaking change* para el frontend actual.
- Añadir campos calculados a operaciones de listado (`GET /obligations`) puede requerir optimización de consultas SQL/N+1.

## 16. Proposed Sprint 6 Implementation Plan
1. **Phase 1: DTO Update**. Modificar Pydantic models (`GoalRead`, `ObligationRead`, `CreditCardRead`) para reflejar los campos requeridos.
2. **Phase 2: Service Update**. Actualizar los repositorios/servicios para poblar estos campos durante las operaciones de listado y obtención.
3. **Phase 3: Snapshot Enrichment**. Añadir la lógica de `free_money` al Snapshot.
4. **Phase 4: Contract Generation**. Regenerar `openapi.json` y validar con tests.

## 17. Questions Requiring Steven Approval
1. ¿Procedemos a reemplazar `estimated_current_debt` por `total_debt` en Credit Cards (breaking change aceptado)?
2. ¿Se debe incluir `free_money` en `IntelligenceSnapshotRead` o el frontend lo consumirá por separado?
3. ¿El historial de conversaciones se maneja en el backend (requiere endpoint GET) o solo en sesión de frontend por ahora?
