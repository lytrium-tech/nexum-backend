> **HISTORICAL NOTICE:** Este documento conserva diseños, planes o reportes históricos. No debe ser considerado la fuente de verdad actual. Para la documentación técnica canónica, consulte [docs/project/](../project/00-backend-overview.md).

# Nexum Core Obligations V1.7 — Safe Implementation Plan

## 1. Objective
Definir un plan de implementación estructurado, seguro y progresivo para reconstruir el módulo de Core Obligations (V1.7) recuperando y expandiendo las capacidades previstas en V1.6, garantizando simultáneamente compatibilidad absoluta con V1.5 (código legacy, frontend y base de datos) durante todo el proceso de transición, sin riesgo de disrupciones en producción. Adicionalmente, integrar una estrategia robusta y responsiva de conversión de monedas (FX) que equilibre una UX instantánea con estrictos principios de integridad financiera backend.

## 2. Core Principles & Non-Negotiable Rules
1. **No DROP TABLE en producción.**
2. **No migraciones destructivas.** Todo cambio en DB es aditivo (Append-Only) o relajante.
3. **No Big Bang release.** Despliegue progresivo por componentes lógicos y Feature Flags.
4. **No cambiar DB/backend/frontend al mismo tiempo sin compatibilidad.**
5. **V1.5 debe seguir funcionando hasta que V1.7 pase QA real.** El sistema debe ser capaz de procesar tráfico legacy permanentemente.
6. **Feature flag obligatorio.** Todos los writes/reads de V1.7 estarán encapsulados.
7. **Backup obligatorio antes de cualquier migración.**
8. **Rollback plan obligatorio antes del deploy.** (Runtime Reversibility: apagando el feature flag).
9. **Tests de contrato frontend/backend obligatorios.**
10. **Tests de dashboard/intelligence obligatorios.**
11. **Tests de usuario nuevo obligatorios.**
12. **Tests de usuario con datos obligatorios.**
13. **No se reaplican NOT NULL de columnas V1.6 hasta migrar datos correctamente.**
14. **Toda nueva columna crítica debe tener default, nullable temporal o backfill seguro.**
15. **Ningún endpoint V1.5 puede romperse durante la transición.**

## 3. Recommended Migration Strategy
**Opción A — Additive migration sobre tablas actuales (Recomendada).**

*Justificación:* V1.5 y V1.6 compartieron la misma base fundacional (`obligations`, `obligation_payments`). En el post-rollback actual, estas tablas ya conviven de forma estable porque las columnas residuales de V1.6 fueron convertidas a nulas. Creando un esquema aditivo sobre estas mismas tablas (junto con la tabla independiente `obligation_periods`) se maximiza la reutilización de consultas legacy y vistas SQL de Inteligencia, minimizando la complejidad de sincronización. Un dual-write temporal es manejable si el modelo se extiende en lugar de reemplazarse.

## 4. Minimum V1.6 Capabilities Preserved
1. **Conceptual Model:** Obligation (Template) -> ObligationPeriod (Instancia pagable) -> ObligationPayment (Aplicación del pago).
2. **Tipos:** Recurrente indefinida, recurrente con fecha final, recurrente con número de ciclos, one-time.
3. **Frecuencias:** Monthly, weekly, biweekly, yearly, one-time.
4. **Montos:** Fijo, variable, `pending_amount_definition` (que no genere deuda falsa).
5. **Pagos:** Completo, parcial, adelantado, a periodo específico, FIFO general, rechazo de sobrepago.
6. **Estados (Periodo):** `pending_amount_definition`, `pending_payment`, `partially_paid`, `paid`, `overdue`, `skipped`, `cancelled`.
7. **Estados (Obligación):** `active`, `completed`, `archived`, `cancelled`.
8. **Ciclos:** Generación controlada (próximo periodo, vencimiento, sin deuda infinita).
9. **Operaciones Auxiliares:** Saltar, cancelar, archivar sin modificar historial pagado.
10. **Multimoneda:** Manejo seguro de currency, cálculos de FX en backend.

## 5. FX Preview & Quote Strategy
Para permitir pagos de obligaciones o aportes desde cuentas en distinta moneda sin fricción, se implementará una UX instantánea apoyada por una arquitectura asíncrona:

### 5.1 Reglas Financieras y de UX
1. Frontend puede calcular una preview visual usando una tasa cacheada obtenida del backend.
2. Esa preview **no es verdad financiera**.
3. **Backend calcula siempre el monto final.**
4. Backend guarda la tasa usada o la referencia de tasa en el evento financiero.
5. Si la tasa cambió significativamente al ejecutar, backend puede devolver un error/response_code controlado.
6. La UI debe mostrar “estimado” o “aproximado” de forma clara.
7. La UI debe mostrar el timestamp de actualización de la tasa.
8. Si la tasa está vencida, frontend puede seguir mostrando el último valor con un warning explícito.
9. Ninguna operación financiera debe depender únicamente del cálculo del frontend; **el LLM nunca calcula dinero.**

### 5.2 Endpoints Conceptuales
```text
GET /api/v1/fx/rates/latest
- Devuelve tasas cacheadas desde proveedor externo (ej. DolarAPI abstraído).
- Incluye: fetched_at, expires_at, provider, stale flag.

POST /api/v1/fx/quote
- Recibe: amount, from_currency, to_currency.
- Backend calcula conversión final.
- Devuelve: source_amount, target_amount, rate, expires_at.
- (Futuro) Devuelve quote_id para congelar la tasa temporalmente.
```

### 5.3 Modelo de Datos Conceptual
```text
Tabla: exchange_rates
- id
- base_currency
- quote_currency
- rate
- provider
- fetched_at
- expires_at
- metadata

Tabla Opcional Futura: fx_quotes
- id, user_id, from_currency, to_currency, source_amount, target_amount, rate, expires_at, status
```

### 5.4 Comportamiento Frontend (Cache Strategy)
1. Al cargar la pantalla de pago, frontend obtiene tasas mediante `GET /api/v1/fx/rates/latest` (apoyado por localStorage, SWR, Next cache, o stale-while-revalidate).
2. Mientras el usuario escribe, el frontend calcula la **preview instantánea** localmente para no bloquear el input.
3. Se refresca la tasa en background cada 60-120 segundos.
4. Al confirmar el pago, el frontend delega al backend la ejecución, donde el backend recalcula la verdad financiera (o valida la cuota si se usó `quote_id`).

### 5.5 UX Copy Recomendado
- Normal: `≈ 2.54 USD` | `Tasa actualizada hace 42 s` | `Valor estimado. El monto final se confirma al pagar.`
- Stale/Vencida: `Usando última tasa disponible. Se confirmará al pagar.`
- Cambio detectado (Backend reject): `La tasa cambió. Confirma el nuevo valor para continuar.`

*(Nota: Considerar en el futuro un documento separado `fx-strategy.md` si el alcance del motor de divisas crece para abarcar Ledger/Goals exhaustivamente).*

## 6. Intelligence / Dashboard Compatibility Strategy
- **Auditoría continua:** Cada vez que se alteren tablas referenciadas en las vistas de inteligencia (`v_pending_obligations_current_month`, etc.), se validará explícitamente que el dashboard siga operando con datos heredados.
- **Backwards Compatibility:** Los estados V1.7 (`paid`, `skipped`) deben reflejarse de forma predecible o aislada de los algoritmos V1.5 de Inteligencia mediante backfill implícito de campos legacy (`is_active`, `amount`).

## 7. Frontend / Backend Contract Strategy
- **Congelamiento inicial:** Antes de codificar la lógica V1.7, se diseñará un esquema OpenAPI y modelos Pydantic teóricos (`app/obligations/schemas_v17.py`).
- **Endpoints independientes o aislados:** Rutas diferenciadas `/api/v1.7/obligations` o uso estricto de header Feature Flags que procesen un Payload V1.7 garantizando que las respuestas no pisen las definiciones de V1.5.

## 8. Implementation Phases

### Fase 0 — Baseline Audit V1.5
- **Objetivo:** Confirmar estado limpio, tests pasando, snapshot DB funcional.
- **Alcance:** Ejecución de tests E2E y unitarios V1.5; revisión de documentación.
- **Criterios de Éxito:** Pipeline 100% verde. Sin dependencias ocultas V1.6 vivas.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 1 — Contract Design V1.7 & FX
- **Objetivo:** Diseñar contratos Pydantic, OpenAPI Spec para V1.7 y Endpoints FX.
- **Alcance:** Definir payloads de obligaciones, responses, error codes, empty states, y contratos de `fx/rates` y `fx/quote`.
- **Criterios de Éxito:** Contratos teóricamente cerrados y revisados.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 2 — Non-Destructive DB Preparation
- **Objetivo:** Preparar el esquema de base de datos de manera estrictamente aditiva.
- **Alcance:** Creación de `obligation_periods`, `exchange_rates` y alteración estructural aditiva de `obligations` y `obligation_payments`. Ningún DROP.
- **Criterios de Éxito:** Migraciones aplicadas en entorno de test sin romper los tests V1.5 actuales.
- **Rollback Específico:** `alembic downgrade`.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 3 — Backend V1.7 & FX Read-Only Endpoints
- **Objetivo:** Implementar capacidades de lectura V1.7 y tasas FX.
- **Alcance:** Endpoints como `list_periods`, `fx/rates/latest`. Fetchers cacheados a proveedores de moneda.
- **Criterios de Éxito:** Lectura de datos sintéticos V1.7 y tasas reales funcionales sin alterar V1.5.
- **Aprobación de Steven para pasar de fase:** NO (Flujo continuo si no se rompe legacy).

### Fase 4 — Backend V1.7 Write Path Behind Feature Flag
- **Objetivo:** Implementar lógica transaccional de creación, pago, FX quote confirm, estados y ciclos.
- **Alcance:** Writes mediante Feature Flag. Lógica FIFO, recálculo FX final y guardado de histórico en `financial_events`.
- **Criterios de Éxito:** Cobertura de pruebas unitarias al 100%.
- **Rollback Específico:** Deshabilitar Feature Flag en router.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 5 — Intelligence/Dashboard Compatibility Audit
- **Objetivo:** Proteger consumidores indirectos.
- **Alcance:** Escribir datos complejos en V1.7 (con conversiones FX) y validar el endpoint `GET /api/v1/intelligence/snapshot`. 
- **Criterios de Éxito:** Intelligence renderiza métricas precisas y equivalencias FX sin crashear.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 6 — Frontend V1.7 Behind Feature Flag
- **Objetivo:** Proveer la interfaz para gestionar V1.7 e UX de FX.
- **Alcance:** Integración UI bajo el Feature Flag V1.7. Cálculos asíncronos de previsualización FX sin bloqueos de escritura.
- **Criterios de Éxito:** Frontend puede comunicarse fluidamente con API V1.7 y cachea tasas exitosamente.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 7 — QA Real (Testing Matrix Execution)
- **Objetivo:** Validar el sistema integral en staging.
- **Alcance Matriz (Expandida):** 
  - Usuario nuevo vs. Usuario con datos V1.5.
  - Tipos: Recurrente, one-time, end-date, end-count.
  - Flujo Variable: obligación variable sin monto -> definición de monto.
  - Flujo Pago: Parcial, completo, adelantado, FIFO.
  - **FX 1:** Pagar obligación COP desde cuenta COP.
  - **FX 2:** Pagar obligación COP desde cuenta USD.
  - **FX 3:** Preview frontend instantánea no bloqueante validada visualmente.
  - **FX 4:** Backend recalcula monto final en persistencia.
  - **FX 5:** Tasa vencida muestra warning.
  - **FX 6:** Cambio de tasa crítico devuelve response_code controlado.
  - **FX 7:** Dashboard refleja monto correcto.
  - **FX 8:** `financial_events` guarda moneda y tasa usada.
  - **FX 9:** Frontend no confía en preview como verdad financiera.
  - Ciclos: Vencimiento (overdue), skipped, cancelled.
  - Trazabilidad: Dashboard snapshot, pending obligations, frontend home.
  - Compatibilidad: Rollback flag-off (V1.5 debe seguir operando intacto).
- **Criterios de Éxito:** Matriz completada sin incidencias críticas.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 8 — Controlled Production Rollout
- **Objetivo:** Desplegar en producción con riesgo cero.
- **Alcance:** Despliegue DB (Backup previo) -> Despliegue Backend -> Habilitación Flag interna -> Rollout total Frontend.
- **Criterios de Éxito:** Producción 100% limpia en logs, usuarios ejecutando sin fallas V1.7 o V1.5.
- **Rollback Específico:** Apagado instantáneo de Feature Flag.
- **Aprobación de Steven para pasar de fase:** SÍ.

### Fase 9 — Legacy Retirement & Data Backfill (Solo si es seguro)
- **Objetivo:** Limpiar deuda técnica V1.5.
- **Alcance:** Data migration scripts para llenar nulos legacy, eliminación gradual de dependencias V1.5 en el backend.
- **Criterios de Éxito:** Producción operando estrictamente en V1.7 de manera consolidada.

## 9. Rollback Strategy
La reversibilidad será gestionada en tiempo de ejecución ("Runtime Reversibility"). Todos los despliegues de V1.7 estarán segregados por versionado de API (`v1.7`) o condicionados por un Feature Flag estructural. Si se detectan anomalías en producción durante la Fase 8, el Feature Flag se apagará sin necesidad de realizar reversiones de código en Git ni de retroceder la estructura de la base de datos, garantizando la recuperación instantánea de la funcionalidad de V1.5. Como las bases de datos no sufrieron mutaciones destructivas, el ORM V1.5 insertará datos nativamente de inmediato sin conflictos.

