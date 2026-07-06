# Nexum V1.7 Phase 7A — Intelligence & Dashboard Compatibility Baseline Audit

## 1. Executive Summary
Esta fase inicial evalúa la compatibilidad de Obligations V1.7 con las necesidades del frontend (Dashboard) y de la capa de inteligencia (LLM). Actualmente, V1.7 implementa un motor transaccional completo y seguro (creación, lectura cruda, pagos específicos, pagos FIFO, FX y Ledger Idempotency), pero carece de endpoints de agregación (summary). 

La auditoría revela que el módulo de `intelligence` de V1.6 fue refactorizado para consumir la tabla `obligation_periods`, lo que lo hace "compatible" estructuralmente con V1.7, pero expone un riesgo crítico: la agregación ciega de montos sin distinción de moneda. Para el dashboard, es imperativo que el backend asuma la responsabilidad de consolidar por moneda y estado antes de enviarlo al frontend.

## 2. Current V1.7 Read Surface
Los endpoints actuales expuestos en V1.7 (`app/obligations/router_v17.py`) son puramente transaccionales y de lectura de entidades crudas:
- `GET /api/v1.7/obligations` (Lista cruda de obligaciones)
- `GET /api/v1.7/obligations/{id}`
- `GET /api/v1.7/obligations/{id}/periods` (Lista de periodos paginada/cruda)
- Modificadores (POST/PATCH para crear, pagar, definir montos, refrescar, saltar, cancelar).

No existe un endpoint `summary` que ofrezca al frontend una vista gerencial del estado del mes.

## 3. Current Dashboard / Frontend Dependencies
El frontend V1.5 y el Dashboard dependen fuertemente de la capa de `/intelligence` (especialmente `/snapshot`, `/cashflow`, `/obligations`):
- `GET /api/v1/intelligence/snapshot`: Retorna `SnapshotObligations` (`pending_count`, `pending_amount`).
- `GET /api/v1/intelligence/obligations`: Retorna una lista plana de periodos pendientes (`amount`, `due_day`, `is_pending`).
- Ambos consultan la tabla `obligation_periods` de forma directa a través de `app/intelligence/repository.py`.

## 4. Available Data for Dashboard
Con los datos V1.7 disponibles en base de datos (`obligations` con `currency`, y `obligation_periods` con estados granulares como `pending_amount_definition`), el dashboard podría teóricamente construir:
- Obligaciones activas, vencidas, pagadas.
- Obligaciones variables pendientes de definición (bloqueantes).
- Pagos realizados (incluyendo metadata FX guardada en `financial_events`).

Sin embargo, exigir que el frontend lea `periods` y calcule estos estados violaría el principio de "Backend calcula".

## 5. Available Data for Intelligence
El LLM tiene acceso potencial a la metadata enriquecida de V1.7 (cross-currency, historial de periodos, recurrencia detallada). Sin embargo, hoy recibe una vista truncada sin monedas a través de los endpoints de `intelligence` legacy. 

## 6. Data Gaps
- **Currency Agnosticism en Intelligence:** La tabla `obligation_periods` expone `amount`, pero `intelligence` asume moneda base local. V1.7 permite crear obligaciones en múltiples monedas (ej. COP, USD). El dashboard mostrará una suma errónea (`$100 USD + $50,000 COP = $50,100`).
- **Estados Ocultos:** `get_pending_obligations` en `intelligence` filtra por `IN ('pending_payment', 'partially_paid', 'overdue')`, omitiendo totalmente `pending_amount_definition`. El usuario nunca verá que debe definir el monto de su servicio de energía mensual.
- **Flujo de Caja Consolidado:** Falta un consolidado que traduzca pagos cross-currency a impacto real en el flujo de caja del usuario.

## 7. Query / Performance Risks
- **Frontend N+1:** Si el frontend se ve forzado a listar obligaciones y luego listar los periodos de cada una para saber qué debe pagar, generaría un patrón N+1 ineficiente y alto tráfico.
- Agregaciones directas en `obligation_periods` sin filtrado eficiente o sin vistas materializadas pueden volverse lentas a medida que se acumulen periodos históricos.

## 8. V1.5 Compatibility Risks
La compatibilidad estructural está a salvo, ya que `intelligence` lee de `obligation_periods`. Sin embargo, la compatibilidad semántica está en riesgo debido al soporte multi-moneda de V1.7. Si un usuario de V1.5 usa una cuenta antigua, no notará el bug. Si crea una obligación en USD, su dashboard (incluso el antiguo) mostrará sumas financieras corruptas. 

## 9. Product Interpretation
- **Backend calcula:** Todo agrupamiento por moneda, conversión FX proyectada, e identificación de estados bloqueantes debe salir del backend.
- **Frontend representa:** El dashboard debe recibir un payload listo para renderizar tarjetas de "Total a Pagar (COP)", "Total a Pagar (USD)" y "Atención Requerida (Por definir monto)".
- **Calma financiera:** El frontend no debe lidiar con conciliaciones FX del ledger.

## 10. Design Questions Answered

1. **¿El dashboard debe consumir directamente periods/payments o un summary endpoint?**
   Debe consumir un summary endpoint. Consumir periods crudos traslada la responsabilidad de agregación multi-moneda al frontend.
2. **¿Debe existir un endpoint /api/v1.7/obligations/summary?**
   Sí. Es indispensable para agrupar deuda pendiente y pagada separada por monedas.
3. **¿Debe existir un endpoint /api/v1.7/obligations/intelligence-context?**
   Sí. Proveerá un prompt semántico o JSON optimizado para LLMs con la visión general de la carga financiera del usuario y los bloqueos (`pending_amount_definition`).
4. **¿Qué debe incluir el summary mensual?**
   Totales pendientes y pagados agrupados por moneda, conteo de periodos vencidos, y conteo/detalle de obligaciones que requieren definición de monto.
5. **¿Qué debe incluir el contexto de IA?**
   Composición de deuda (fija vs variable), alertas de moneda extranjera, y requerimientos de acción inmediata.
6. **¿Qué métricas deben ser calculadas por backend y no por frontend?**
   Sumas agregadas por moneda, estatus de riesgo (vencido), y totales proyectados.
7. **¿Qué datos deben mantenerse fuera del frontend por simplicidad?**
   Ids de transacciones ledger, UUIDs de sub-quotes FX, y mecánicas de FIFO.
8. **¿Cómo se deben representar obligaciones variables pendientes de monto?**
   Como una categoría explícita ("Action Required" o "Pending Definition") separada de la suma financiera estricta, pues un valor `null` o `0` provisorio no puede sumarse al total de deuda.
9. **¿Cómo se deben representar pagos cross-currency?**
   En el resumen, se reporta el impacto en la moneda de la cuenta origen (cashflow out) y la satisfacción de la cuota en su moneda destino, sin forzar una vista contable compleja.
10. **¿Qué compatibilidad necesita el frontend V1.5 actual?**
   Necesita que `/intelligence/snapshot` maneje multi-moneda o devuelva un fallback seguro, y que exponga obligaciones en `pending_amount_definition` para que el usuario no las ignore.

## 11. Recommended Phase 7B Scope
**Scope propuesto para Phase 7B:**
1. Crear `GET /api/v1.7/obligations/summary` que retorne una vista de alto nivel agrupada por moneda (`totals_by_currency`, `requires_action`).
2. Actualizar/parchar `app/intelligence/repository.py` (`get_pending_obligations`) para que no corrompa sumas sumando montos de distintas monedas y para que reporte estados `pending_amount_definition`.
3. (Opcional) Crear `GET /api/v1.7/obligations/intelligence-context` read-only.
*Nota: Ningún código frontend debe modificarse en Phase 7B.*

## 12. Production Safety
- **Producción:** Intacta. No se tocó DB, ni código, ni migraciones.
- **Estatus:** 100% aislado.

## 13. Issues / Blockers
- **Blocker Arquitectónico Identificado:** El endpoint global actual de intelligence suma ciega de `op.amount`. Esto causará corrupción de UI cuando se creen obligaciones V1.7 en USD. Debe parcharse.
