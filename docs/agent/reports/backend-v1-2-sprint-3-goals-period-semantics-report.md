# Backend V1.2 Sprint 3 — Goals Period Semantics Report

## 1. Executive Summary
El Sprint 3 fue completado exitosamente. Se corrigió y amplió la semántica periódica de las metas financieras, garantizando que el backend asuma la responsabilidad de redondear requerimientos y aportes según la unidad mínima operativa de cada moneda. Además, se definieron estados de período claros (`flexible`, `completed`, `pending`, `partial`, `covered`, `overfunded`) y se incorporaron campos adicionales solicitados.

## 2. Problem Fixed
Previamente, el backend retornaba montos requeridos exactos (e.g., `33333.33` para COP), obligando al Frontend a manejar redondeos y conversiones a pesar de que el backend definió la unidad mínima de `50 COP`. Además, la lógica del `period_status` carecía de estados claros como `overfunded`. 

## 3. Goal Contract Before
- `monthly_required` devolvía un decimal sin redondear por moneda (e.g. `round(amount, 2)`).
- `required_this_period` dependía del valor sin redondear.
- Faltaban los campos `daily_required_this_period`, `days_remaining_in_period` y `currency_minimum_unit`.
- Carecía del campo de moneda en el esquema `GoalRead` (aunque estaba en la BD).

## 4. Goal Contract After
Se agregó:
- `currency`: Propagado desde la tabla `goals` al esquema de lectura.
- `currency_minimum_unit`: Unidad mínima expuesta para el cliente.
- `days_remaining_in_period`: Días faltantes en el mes.
- `daily_required_this_period`: Requisito diario para alcanzar la cuota del período.
- Los campos numéricos ahora están redondeados hacia arriba según la regla de la moneda de la meta.

## 5. Currency Minimum Unit Usage
Se extendió `app.core.currency` añadiendo `round_up_to_minimum_unit(amount, currency)`.
- `COP` → 50
- `USD`/`EUR` → 0.01
Se usó en el cómputo de `monthly_required` y `daily_required_this_period`.

## 6. Rounding Strategy
Todo requerimiento (mensual, diario) es redondeado financieramente hacia arriba (e.g., `ROUND_CEILING` respecto al múltiplo de la unidad mínima).
`33333.33 COP` se convierte en `33350 COP`.
`33.3333 USD` se convierte en `33.34 USD`.
Esto garantiza que la meta nunca quede infrafinanciada.

## 7. Period Status Semantics
Se definieron los siguientes estados para `period_status`:
- `completed`: Si se alcanzó el objetivo total.
- `flexible`: Si no tiene fecha límite.
- `pending`: Aporte en el periodo es 0 pero se requiere aporte.
- `partial`: Aporte mayor a 0 pero menor al requerido.
- `covered`: Aporte igual al requerido.
- `overfunded`: Aporte superior al requerido en el período.

## 8. Daily Required Calculation
Calculado dividiendo `remaining_required_this_period` entre `days_remaining_in_period`, aplicando el mismo redondeo hacia arriba. Si restan 0 días, asume la cuota restante completa. Si ya está cubierta, devuelve 0.

## 9. Flexible Goals
Las metas sin `target_date` exponen:
- `monthly_required = 0`
- `required_this_period = 0`
- `remaining_required_this_period = 0`
- `period_status = "flexible"`

## 10. Backward Compatibility
Los atributos preexistentes (`monthly_required`, `required_this_period`, `remaining_required_this_period`, `period_status`) conservan su tipado en `Decimal` y `str`, garantizando compatibilidad con Frontend V1.1. Los alias heredados siguen siendo funcionales.

## 11. Tests
Se introdujo `tests/unit/test_goals_semantics.py` para verificar:
- Redondeo COP a múltiplos de 50 (hacia arriba).
- Redondeo USD/EUR a 0.01 (hacia arriba).
- Reglas de flexibilidad (`is_flexible = True`).
- Computo correcto sin centavos invisibles en `remaining_required_this_period`.
- Semántica de `daily_required_this_period`.
La suite de 154 tests pasa completamente.

## 12. OpenAPI / Docs
- Se regeneró `openapi.json` con los nuevos campos en `GoalRead`.

## 13. Risks
- Frontend V1.1 podría no aprovechar inmediatamente los campos `daily_required_this_period` o `days_remaining_in_period`, pero la compatibilidad de base no se rompió.

## 14. Final Status
El Sprint 3 se declara completo. Todo el código pasa pruebas, la verificación estática de Ruff es satisfactoria y el diseño semántico se ajusta a las directrices.
