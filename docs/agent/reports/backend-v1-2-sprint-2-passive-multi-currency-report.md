# Backend V1.2 Sprint 2 — Passive Multi-Currency Report

## 1. Executive Summary
El Sprint 2 fue completado exitosamente. Se ha establecido un módulo de configuración para `currency minimum units` para manejar diferentes monedas de forma centralizada. Además, el endpoint `get_snapshot` se extendió para evitar cruzar saldos entre distintas divisas, e introdujo el nodo `totals_by_currency` con métricas exclusivas por moneda.

## 2. Problem Fixed
El snapshot original mezclaba saldos de cuentas en distintas divisas (`COP`, `USD`, etc.) al calcular métricas como `total_balance` y el flujo de caja, exponiendo valores financieramente incorrectos (e.g., sumar 10 USD a 50,000 COP como 50,010 globalmente). Además, las unidades mínimas permitidas de cada moneda no estaban centralizadas.

## 3. Currency Model Audit
Se confirmó que el campo `currency` ya existía previamente en las entidades principales de la base de datos (`accounts`, `credit_cards`, `financial_events`, `goals`, `obligations`, `users`) con un default `COP`. Por lo tanto, no hubo necesidad de ejecutar migraciones de base de datos y la recolección de datos ya venía segmentada. 

## 4. Currency Minimum Units
Se implementó `app.core.currency` con las reglas iniciales solicitadas:
- COP -> 50
- USD -> 0.01
- EUR -> 0.01
- fallback -> 0.01

## 5. Snapshot Contract Before
El `IntelligenceSnapshotRead` devolvía únicamente métricas globales en la raíz de sus componentes (e.g. `cash.total_balance`). Si había múltiples monedas, los valores numéricos se sumaban directamente ignorando la divisa.

## 6. Snapshot Contract After
Se mantiene la compatibilidad de contratos legacy, pero se agregan campos específicos.
Se introdujo `totals_by_currency` como un diccionario mapeado por código de moneda.
Se introdujo el arreglo `calculation_warnings` dentro del nodo `truth` para alertar cuando existan múltiples divisas que distorsionen los totales calculados globalmente.

## 7. Totals By Currency
Cada moneda computa independientemente:
- `available_real`
- `committed_outflows`
- `free_money`
- `safe_money`
- `income_current_period`
- `cash_expenses_current_period`
- `credit_card_consumption_current_period`
- `debt_payments_current_period`
- `goal_contributions_current_period`
- `obligation_payments_current_period`
- `net_cashflow_current_period`

## 8. No Cross-Currency Aggregation Rule
Debido a que el Frontend v1.1 espera las propiedades legacy (como `truth.free_money`), los valores globales aún se retornan. No obstante, se incluyeron guardrails que generan advertencias (`calculation_warnings`) en caso de que existan transacciones o cuentas asociadas a múltiples divisas (esto indicará al Frontend que no debe confiar ciegamente en los saldos agregados si no manejan un FX).

## 9. Backward Compatibility
Los campos originales del contrato `SnapshotCash`, `SnapshotCashflow`, `SnapshotTruth` siguen existiendo para no romper el cliente web (aunque para cuentas multimoneda puedan estar distorsionados). `totals_by_currency` es completamente opt-in para nuevas vistas.

## 10. Tests
- Se añadieron tests `tests/unit/test_currency.py` para validar redondeos con `COP` (50) y divisas fraccionarias (`USD`).
- Se verificó que todas las pruebas existentes de `IntelligenceService` funcionan correctamente tras agregar la agrupación de multidivisa (145 passed).

## 11. OpenAPI / Docs
- `openapi.json` se regeneró en UTF-8 y se actualizó de acuerdo a las adiciones en los esquemas Pydantic del Snapshot y `CreditCardStatusRead`.

## 12. Risks
El cálculo unificado global no fue eliminado. Si un usuario opera de forma sustancial en múltiples monedas, su valor global heredado (legacy) sumará peras con manzanas.

## 13. Final Status
El Sprint 2 está validado, sus reportes producidos y `pytest` / `ruff` fueron aprobados. Todo está listo para commit y push.
