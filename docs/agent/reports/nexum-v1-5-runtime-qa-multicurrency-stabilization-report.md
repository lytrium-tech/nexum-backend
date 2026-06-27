# Nexum V1.5 — Runtime QA Multi-Currency & Obligation/Credit Stabilization Report

Este reporte documenta los diagnósticos y correcciones implementadas para resolver los blockers financieros y de servidor detectados durante la QA de Runtime de Nexum V1.5.

## 1. Executive Summary

Se diagnosticaron y solucionaron 3 problemas principales que bloqueaban la QA:
1. **Errores de conversión multimoneda** en pagos de obligaciones y tarjetas de crédito (se permitía la conversión, pero el backend no realizaba los cálculos y validaciones en la moneda destino correcta, o generaba fallos de validación de enums).
2. **Crashes (500) en creación de obligaciones** al usar nombres duplicados (retornaba ValueError no capturado en lugar de un error 409/400 limpio).
3. **NoneType Error** en el flujo de pagos anticipados (`pay_early`) de tarjetas de crédito al calcular cuotas amortizadas con `paid_amount` inicializado en `None`.

Todos los cambios fueron validados localmente mediante **14 nuevos escenarios de pruebas unitarias**, y la suite completa de **215 pruebas del backend pasó exitosamente**.

---

## 2. Diagnóstico y Correcciones Detalladas

### A. Conversión Multimoneda en Pagos de Obligaciones
* **Síntoma:** Al pagar una obligación COP con una cuenta USD (o viceversa), el sistema no realizaba la conversión correspondiente antes de evaluar límites o montos fijos, o fallaba en la persistencia.
* **Solución:**
  * Se movió la carga de la cuenta al inicio del método `create_payment` de `ObligationService`.
  * Si la moneda de origen difiere de la de destino (obligación), se consulta la tasa a través de `get_fx_rate` y se redondea el monto convertido usando `round_to_minimum_unit`.
  * Las validaciones de cuota fija (`fixed_full_payment`) o límites en pagos parciales (`partial_allowed`) ahora se evalúan en base al monto convertido (`applied_amount`) en la moneda de la obligación.
  * Se almacena el detalle completo del tipo de cambio (tasa, fuente, timestamp e indicativo de estimación) en la metadata de `LedgerEvent` y `ObligationPayment`.

### B. Conversión Multimoneda en Pagos y Abonos Anticipados de Tarjetas
* **Síntoma:** Los pagos ordinarios y abonos anticipados a compras de tarjetas de crédito fallaban al procesar monedas mixtas y generaban errores de validación de esquemas/enums (`credit_card_early_payment` no era un tipo de evento financiero válido).
* **Solución:**
  * Se implementó el mismo motor de consulta FX en `create_payment` y `create_early_payment` de `CreditCardService`.
  * Se corrigió el tipo de evento registrado en el Ledger para abonos anticipados a `"credit_card_payment"` (el cual sí es un enum válido en `EventType`), almacenando los detalles de conversión en el campo de metadata de `CreditCardEarlyPayment` y `CreditCardTransaction`.

### C. NoneType Safety en Abonos Anticipados (`pay_early`)
* **Síntoma:** Al intentar abonar a una compra que no tenía abonos previos registrados (`paid_amount=None` en la base de datos), el sistema arrojaba un error de tipo:
  `TypeError: '<' not supported between instances of 'decimal.Decimal' and 'NoneType'`
* **Solución:**
  * Se sanitizaron las lecturas de `paid_amount` en `create_early_payment` y en el waterfall del service reemplazándolas por `Decimal("0.00")` si el valor es nulo.

### D. Manejo de Errores de Validación de Nombres en Obligaciones
* **Síntoma:** Al intentar crear o actualizar una obligación con un nombre que ya existía de forma activa, el servicio lanzaba un `ValueError` genérico que el API traducía a un HTTP 500 (Internal Server Error) feo para el usuario.
* **Solución:**
  * Se definieron `ObligationValidationError` y `ObligationDuplicateError` heredando de `ValidationError/ConflictError` y `ValueError` simultáneamente (preservando compatibilidad con aserciones de pruebas existentes).
  * Esto permite que FastAPI capture el error con el handler de `ConflictError` y retorne un código de respuesta HTTP 409 Conflict o 400 Bad Request con un mensaje limpio explicativo.

---

## 3. Cobertura de Pruebas Unitarias

Se creó el archivo [test_multicurrency_qa.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/tests/unit/test_multicurrency_qa.py) con 14 test cases que simulan y validan los siguientes flujos críticos:
1. Pago de obligación en la misma moneda COP -> COP.
2. Pago de obligación en la misma moneda USD -> USD.
3. Pago cruzado de obligación COP -> USD (con conversión y redondeo).
4. Pago cruzado de obligación USD -> COP.
5. Creación de obligación variable con abono inicializado.
6. Ciclo de vida de obligación atrasada (pasa a pagada en el periodo actual tras recibir pago, y se reactiva como pendiente en el siguiente mes utilizando congelamiento de tiempo `freezegun`).
7. Intento de conversión con par de monedas no soportado (EUR -> COP) retorna error controlado 403.
8. Validación de fondos insuficientes en cuenta origen evaluada en la moneda origen, no destino.
9. Pago de tarjeta de crédito en misma moneda COP -> COP.
10. Pago de tarjeta de crédito en misma moneda USD -> USD.
11. Pago de tarjeta de crédito cruzado COP -> USD.
12. Pago de tarjeta de crédito cruzado USD -> COP.
13. Abono anticipado a compra de tarjeta con `paid_amount=None` (validando NoneType safety).
14. Abono anticipado cruzado de tarjeta de crédito con par soportado.

---

## 4. Estado de la Suite de Pruebas

```text
Pruebas Totales: 215
Pruebas Exitosas: 215
Pruebas Fallidas: 0
Format & Lint (Ruff): Clean (passed)
```

---

## 5. Recomendación y Próximos Pasos

El backend ha sido estabilizado y todos los bugs de QA de Runtime reportados han sido corregidos.
Se propone:
1. Obtener la aprobación de Steven para registrar los cambios en la rama de backend mediante un commit limpio.
2. Desplegar a producción para reiniciar las pruebas con el frontend.
