# Nexum Backend V1.7 — Reporte de Corrección de Débito en Pagos Multidivisa

Este reporte documenta el diagnóstico, la corrección y las pruebas realizadas para el error en el descuento de saldo al confirmar pagos cruzados (multidivisa) en Nexum V1.7.

## 1. Diagnóstico del Error

### Comportamiento Reportado
Durante el flujo de QA en el que un usuario intenta realizar un pago desde una cuenta en una moneda diferente a la de la obligación (por ejemplo, Cuenta en `USD` hacia una Obligación en `COP`):
* El preview/quote inicial calcula correctamente la cotización (por ejemplo, pagar 20,000 COP descuenta ~5 USD).
* Al confirmar y aplicar el pago, el backend debitaba **20,000** (el monto de la obligación en COP) de la cuenta de origen (USD) en lugar de los **5 USD** indicados por la cotización (`source_amount`).

### Causa Raíz
Tanto en `pay_specific_period` como en `pay_obligation_fifo` (dentro de [service_v17.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/app/obligations/service_v17.py)), el backend utilizaba la siguiente lógica para detectar si el pago era multidivisa y aplicar la cotización:
```python
if data.source_currency and data.source_currency != obligation.currency:
    # Lógica de pago multidivisa (debitar quote.source_amount)
    ...
else:
    # Lógica de pago misma moneda (debitar data.amount)
    ...
```
Sin embargo:
1. El frontend de la aplicación en Nexum no envía `source_currency` en el payload de confirmación del pago (únicamente envía `amount`, `source_account_id` y `quote_id`).
2. Como `data.source_currency` era `None`, el bloque condicional caía en el `else` (misma moneda), debitando `data.amount` (20,000 COP) directamente de la cuenta USD sin considerar la tasa de cambio ni el `source_amount` de la cotización (`quote.source_amount`).

---

## 2. Solución Implementada

Se modificaron `pay_specific_period` y `pay_obligation_fifo` en [service_v17.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/app/obligations/service_v17.py) de la siguiente manera:

1. **Resolución Dinámica de Monedas**:
   * Si se especifica `source_account_id`, el backend carga la cuenta origen desde el repositorio (`account_repo.get_by_id`) y extrae su moneda real.
   * Si no hay `source_account_id` pero sí `source_currency` en la petición, se mantiene esta última como fallback de compatibilidad.
   * Si no se indica ninguna de las dos, se asume la moneda de la obligación (mismo comportamiento de fallback).

2. **Validación Estricta de Cotización (FXQuote)**:
   * Si el pago es multidivisa (`source_currency != obligation.currency`), se requiere obligatoriamente una cotización activa (`quote_id`).
   * Se añadieron validaciones de integridad de la cotización:
     * El `target_amount` de la cotización debe coincidir exactamente con el `amount` solicitado a pagar.
     * La moneda origen de la cotización (`quote.from_currency`) debe ser la misma que la de la cuenta origen (`source_currency`).
     * La moneda destino de la cotización (`quote.to_currency`) debe ser la misma que la de la obligación (`obligation.currency`).
   * Al pasar todas las validaciones, se define `source_amount = quote.source_amount` y se debitan `source_amount` de la cuenta de origen.

3. **Corrección de Test Unitario Existente**:
   * En `test_pay_specific_period_cross_currency_success`, se corrigió el mock de `user_id` para que use `uuid.UUID` en lugar de una cadena (`str`), resolviendo discrepancias de tipos al consultar las cotizaciones simuladas.

---

## 3. Pruebas y Validación Local

Se implementaron dos nuevas pruebas unitarias robustas en [test_obligations_v17_api.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/tests/unit/test_obligations_v17_api.py):
1. **`test_pay_specific_period_cross_currency_debits_source_amount_not_applied_amount`**:
   * Valida de manera integral que una cuenta origen en `USD` de un saldo inicial de `100.00` reciba un débito correspondiente al `source_amount` de la cotización (`5.00 USD`) y termine en `95.00 USD`, en lugar de restar los `20,000 COP`.
2. **`test_pay_specific_period_cross_currency_validations`**:
   * Caso 1: Pagos multidivisa sin cotización fallan con `422 Unprocessable Entity - quote_required`.
   * Caso 2: Pagos donde el monto no coincide con la cotización fallan con `422 Unprocessable Entity - invalid_fx_quote`.
   * Caso 3: Pagos con cotizaciones cuya moneda origen no coincide con la de la cuenta origen fallan con `422 Unprocessable Entity - invalid_fx_quote`.

### Ejecución de Pruebas
Se corrió toda la suite de pruebas del módulo de obligaciones V1.7 y del backend en general, resultando exitosas:
```bash
pytest -k "v17 and obligation" -v
pytest -v
```
**Resultado**: `268 passed, 3 skipped, 170 warnings` (Todos los tests aprobados).
