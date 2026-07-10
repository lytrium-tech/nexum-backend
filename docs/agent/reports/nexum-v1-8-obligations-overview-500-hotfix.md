# NEXUM V1.8 — HOTFIX OVERVIEW ENDPOINT 500

## 1. Stacktrace Real

```text
NameError: name 'Decimal' is not defined
  File "/app/app/obligations/service_v17.py", line 171, in list_obligations_overview
    payable_total = Decimal("0")
```

## 2. Root Cause

La clase `ObligationV17Service` no tenía la importación global de `Decimal` ya que en las otras funciones importaba de forma local (`from decimal import Decimal`). Al crear `list_obligations_overview`, se utilizó `Decimal("0")` en la línea 171 sin incluir la importación, lo que desencadenaba el error 500 en producción.

## 3. Fix Aplicado

**Backend:**
- Se agregó `from decimal import Decimal` en la cabecera de la función `list_obligations_overview`.
- El endpoint overview sigue devolviendo 200 con o sin periodos (`relevant_period = None`, `period_counts` en cero).
- `ob.base_amount` opcional se maneja de forma segura mapeando a `Decimal("0")` si es `None`.
- N+1 se mantiene eliminado, no se han revertido las optimizaciones.

**Frontend:**
- No se han requerido arreglos en frontend. El cliente ya maneja el caso de que la respuesta venga según el modelo. El error era estrictamente backend.

## 4. Tests Añadidos

Se han cubierto los casos borde y de legado en `tests/unit/test_obligations_v17_overview.py`:
- `test_overview_returns_200_with_obligation_without_periods`
- `test_overview_handles_legacy_obligation_missing_optional_fields`

## 5. Próximos Pasos

El fix backend ya está validado localmente con las pruebas. Requiere commit backend, push y deploy. Frontend se mantiene tal cual y puede seguir con el QA manual posterior al deploy de esta corrección.
