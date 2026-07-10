# Nexum V1.7 — FIFO no_payable_periods Debug Report

## Contexto
Durante el QA de Steven del pago FIFO V1.7, al intentar pagar la obligación recurrente `28514b95-5b3d-4fdc-af5a-33b6550d07c2`, el backend devolvió `422 no_payable_periods`. Sin embargo, la UI mostraba que el periodo estaba "Vencido" y permitía intentar el pago.

## Análisis Backend
La investigación del endpoint FIFO `pay_obligation_fifo` (línea 596 en `app/obligations/service_v17.py`) reveló que la consulta `stmt` para buscar periodos pagables filtraba por:
```python
ObligationPeriod.status.in_(
    [
        PeriodStatus.pending_payment.value,
        PeriodStatus.partially_paid.value,
    ]
)
```
Sin embargo, `PeriodStatus.overdue` no estaba incluido en la lista. Como resultado, cualquier periodo que alcanzara el estado `overdue` (vencido) era ignorado por la lógica de FIFO, devolviendo el error `no_payable_periods` (Caso C: Filtro FIFO demasiado estricto).

## Resolución y Fixes
1. **Backend**: Se modificó `app/obligations/service_v17.py` añadiendo explícitamente `PeriodStatus.overdue.value` en la lista de estados considerados pagables para FIFO. Esto asegura que la lógica priorice correctamente los periodos más antiguos y vencidos.
2. **Frontend**: Se añadió un mapeo amigable para el usuario en `v17-actions.ts`:
   - `no_payable_periods` → "No hay periodos pendientes disponibles para pago."
   - `period_not_payable` → "Este periodo no está disponible para pago."

## QA y Validaciones Automáticas
- Backend: Se ejecutaron `pytest` (para `v17 and obligation`, `fifo`, y `period`), pasando todos los tests (45 pasados).
- Frontend: `pnpm lint` sin errores y `pnpm build` ejecutado exitosamente.

## Estado Final
- **Causa Raíz**: `PERIOD_STATUS_FILTER_WRONG` (filtro FIFO demasiado estricto excluía `overdue`).
- **QA Recomendado**: Re-intentar el pago FIFO localmente para verificar que el periodo vencido se paga y debita correctamente.
- **Estabilidad V1.7**: Pendiente QA manual, no declarar estable aún.
