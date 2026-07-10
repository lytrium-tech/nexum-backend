# NEXUM V1.8 — FIX OBLIGATIONS SCREEN N+1 PERIOD LOAD

## 1. Problema Confirmado
La pantalla `/app/obligations` presentaba un problema de rendimiento N+1.
El frontend, al iterar sobre el listado de `obligations`, llamaba concurrentemente a `getObligationPeriodsV17Action(ob.id)` para cada obligación con el objetivo de cargar sus periodos.

## 2. Solución Aplicada
### Backend
- Se implementó `list_obligations_overview` en `ObligationV17Service`. Este método consulta las obligaciones e inmediatamente efectúa un query ordenado de todos los periodos de dichas obligaciones (usando `where(ObligationPeriod.obligation_id.in_(obligation_ids))`).
- Se expuso `GET /api/v1.7/obligations/overview`.
- El endpoint calcula en memoria para cada obligación: `relevant_period`, contadores de periodos `period_counts`, y estado de las acciones `action_state`.

### Frontend
- Se agregó `getObligationsOverviewV17Action` al cliente API y a los actions.
- `ObligationsV17Client` ahora llama a este endpoint único en su ciclo de carga inicial (reemplazando `listObligationsV17Action` y el iterativo de `getObligationPeriodsV17Action`).
- El estado local `periodsByObligation` fue eliminado y la UI fue reescrita para consumir el `ob.relevant_period` devuelto directamente por la API.

## 3. Pruebas y Validación
- Backend: Pruebas unitarias de overview completas (con `relevant_period`, `action_state`, variables pendientes de monto, etc.). Todo el suite de pruebas unitarias v1.7 pasó.
- Frontend: Lint y build exitosos. Se reemplazó el N+1 por una sola consulta `GET /overview`.

## 4. Estado del Sistema
Esta mejora fue validada localmente en frontend contra el entorno backend de producción para verificar el desempeño sin impacto a la lógica core de Obligaciones (FIFO, Snapshot, Skip, etc).
