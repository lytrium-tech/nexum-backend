# Backend V1.2 Sprint 1 — Snapshot Period Semantics Report

## 1. Executive Summary
El Sprint 1 ha finalizado. Se corrigió la semántica del Snapshot, aislando completamente las métricas del periodo actual, resolviendo duplicidades entre consumos con tarjeta y pagos de deuda, e introduciendo un bloque histórico para backward compatibility sin ensuciar la foto financiera de corto plazo.

## 2. Problem Fixed
El Snapshot/Home combinaba históricamente flujos que no distinguían entre compras a crédito y salidas de dinero real. Esto generaba que el "net cashflow" fuera ambiguo y sumara gastos duplicados cuando el usuario pagaba una tarjeta de crédito. También carecía de granularidad para periodos actuales (el mes en curso).

## 3. Snapshot Contract Before
`SnapshotCashflow` exponía únicamente:
- `income`
- `expenses`
- `net_cashflow`
Sin claridad si contenían pagos a metas, deudas o tarjetas de crédito.

## 4. Snapshot Contract After
Se añadió el nodo `historical` para aislar métricas acumuladas de periodos anteriores.
`SnapshotCashflow` ahora expone con precisión del periodo actual:
- `income_current_period`
- `cash_expenses_current_period`
- `credit_card_consumption_current_period`
- `debt_payments_current_period`
- `goal_contributions_current_period`
- `obligation_payments_current_period`
- `committed_outflows_current_period`
- `net_cashflow_current_period`

## 5. Current Period Metrics
Todas las métricas de `_current_period` son servidas mediante SQL explícito que evalúa `event_type` en la tabla `financial_events`, filtrando estrictamente los eventos cuyo `occurred_at` está dentro del mes en curso.

## 6. Historical Metrics
Se introdujo la estructura `HistoricalCashflow` como un nodo separado `historical` en la respuesta raíz del snapshot. Calcula todos los ingresos y salidas consolidadas previas al `month_start`.

## 7. Cashflow Subtypes
La consulta de la base de datos ahora agrupa explícitamente los subtipos en lugar de resumirlo todo en "expense":
- `credit_card_purchase` = consumo con crédito
- `credit_card_payment` = debt_payment
- `goal_contribution` = ahorro para meta
- `obligation_payment` = pago de obligación fija

## 8. Net Cashflow Formula
```text
net_cashflow_current_period = 
    income_current_period
    - cash_expenses_current_period
    - goal_contributions_current_period
    - obligation_payments_current_period
    - debt_payments_current_period
```
Nota: `credit_card_consumption_current_period` se excluye de la fórmula de net cashflow porque el consumo a crédito no implica salida de efectivo real hasta que se paga la tarjeta (`debt_payments_current_period`).

## 9. Backward Compatibility
Los campos originales (`income`, `expenses`, `net_cashflow`) en el contrato `SnapshotCashflow` se preservaron con la suma de flujos para no romper el Frontend V1.1. Se marcaron como `[DEPRECATED]` en el schema OpenAPI indicando explícitamente que deben reemplazarse por las variantes del `current_period`.

## 10. Tests
- Se añadieron y adaptaron las variables del repositorio `get_cashflow_metrics` y `get_historical_cashflow_metrics` en los mocks de pruebas asíncronas.
- Ejecutado `pytest tests/ -v`: 142 pruebas pasadas.
- Confirmado en tests que transferencias no afectan cashflow y que las salidas de caja (goals, obligations, cash) descuentan según la fórmula.

## 11. OpenAPI / Docs
- `openapi.json` actualizado y regenerado exitosamente.

## 12. Risks
La retrocompatibilidad dependerá de que Frontend migre en Sprint posteriores a los nuevos nodos `_current_period`. Se deben comunicar los campos deprecados en el handoff.

## 13. Final Status
Completado exitosamente. `working tree` con cambios limpios y validados por Ruff y Pytest.
