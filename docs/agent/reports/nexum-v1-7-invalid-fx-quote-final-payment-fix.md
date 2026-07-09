# Nexum Backend V1.7 — Reporte de Corrección de Cotización Inválida (`invalid_fx_quote`)

Este reporte documenta el diagnóstico, la solución y las pruebas del error `invalid_fx_quote` al confirmar pagos multidivisa en Nexum V1.7.

## 1. Diagnóstico del Error

### Comportamiento Reportado
Durante el flujo de QA en Nexum V1.7:
1. El preview de pago calcula y muestra la estimación correcta (ej: pagar 20,000 COP descuenta ~5.98 USD).
2. Sin embargo, al hacer clic en confirmar el pago, la petición de pago final retorna un error `422 Unprocessable Entity - invalid_fx_quote`, bloqueando la confirmación y el débito.

### Causa Raíz
Se identificaron dos problemas principales que causaban `invalid_fx_quote`:

1. **Discrepancia del Proveedor de Tasa de Cambio**:
   * En `pay_preview_specific_period`, la tasa de cambio de vista previa se obtenía de forma dinámica llamando a `get_fx_rate()` (la cual utiliza la API real `DolarAPI` para cotizaciones en producción).
   * Sin embargo, al guardar la cotización (`FXQuote`) en base de datos para consumo posterior del pago final, se creaba usando de forma hardcodeada el `StaticFxRateProvider()` (el cual retorna una tasa fija de `4000.00`).
   * Al ser tasas distintas, el valor guardado en base de datos (`quote.target_amount = source_amount * 4000.00`) difería enormemente del monto ingresado original.

2. **Diferencias por Redondeo del Saldo Fuente**:
   * Para calcular `source_amount`, el preview divide `applied_amount` entre `fx_rate` y redondea el resultado a la unidad mínima de la cuenta de origen (USD centavos, es decir, 2 decimales).
   * Al reconstruir y persistir la cotización en `create_quote`, se multiplica de vuelta `source_amount * rate` para calcular `target_amount`.
   * Debido al redondeo previo en centavos, este cálculo inverso rara vez coincide de forma matemática exacta con el `applied_amount` original. Por ejemplo, `20000 COP / 4123.45` = `4.85 USD` -> `4.85 * 4123.45` = `19998.73 COP`.
   * El backend comparaba de forma estricta e igualitaria (`quote.target_amount != data.amount`), por lo que cualquier discrepancia mínima por redondeo (como `19998.73 != 20000.00`) lanzaba un error `invalid_fx_quote`.

---

## 2. Solución Implementada

Se realizaron las siguientes modificaciones en [service_v17.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/app/obligations/service_v17.py):

1. **Alineación de Tasas de Cambio en Preview**:
   * Se modificó `pay_preview_specific_period` para que inicialice un `StaticFxRateProvider` configurado dinámicamente con la tasa exacta recuperada de `get_fx_rate()`.
   * Esto garantiza que tanto la visualización de la vista previa como la persistencia de la cotización usen exactamente la misma tasa, previniendo discrepancias por proveedores de tasas.

2. **Tolerancia del 0.10% por Redondeo (y límite absoluto)**:
   * En `pay_specific_period` y `pay_obligation_fifo`, se reemplazó la comparación directa de montos con una tolerancia estricta pero suficiente para absorber diferencias de redondeo de centavos:
     ```python
     allowed_diff = max(
         data.amount * Decimal("0.001"),
         Decimal("50.00") if obligation.currency == "COP" else Decimal("0.01")
     )
     if abs(quote.target_amount - data.amount) > allowed_diff:
         raise HTTPException(status_code=422, detail="invalid_fx_quote")
     ```

3. **Manejo de expiración**:
   * El TTL de las cotizaciones se limitó estrictamente a 90 segundos.
   * Se mapeó el error de cotización expirada a `fx_quote_expired`.

---

## 3. Pruebas y Validación Local

Se implementaron y ejecutaron con éxito las siguientes pruebas en [test_obligations_v17_api.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/tests/unit/test_obligations_v17_api.py):

1. **`test_cross_currency_preview_quote_can_be_used_for_payment`**:
   * Simula un flujo completo (end-to-end): solicita la vista previa, obtiene el `quote_id`, y lo confirma exitosamente debitando el equivalente en divisa sin fallar (90s TTL comprobado de forma natural al usarlo de inmediato).
2. **`test_pay_specific_period_cross_currency_tolerance_validation`**:
   * Verifica que discrepancias menores por redondeo decimal (ej. cotización de `19998.73 COP` vs pago solicitado de `20000 COP`, dif 1.27) sean aceptadas bajo la nueva tolerancia del 0.10% (max 50 COP) y se procese el pago correctamente.
3. **`test_pay_specific_period_cross_currency_mismatched_amount_validation`**:
   * Verifica que si los montos difieren por un margen mayor al límite estricto de la tolerancia (ej: diferencia de `51.00 COP` en un pago de 20000 COP), el backend lo rechace con `422 Unprocessable Entity - invalid_fx_quote`.
4. **`test_pay_specific_period_cross_currency_expired_quote`**:
   * Verifica el comportamiento determinista devolviendo explícitamente `fx_quote_expired` cuando una cotización enviada por el usuario supera los 90 segundos.

Todas las pruebas corrieron exitosamente: `26 passed, 249 deselected`.

---

## 4. FX Quote Policy Decision

En el marco de la corrección, se refinaron las políticas comerciales respecto al UX del usuario y la robustez financiera de Nexum V1.7:

- **TTL elegido**: `90 segundos` (`1.5 minutos`).
- **Tolerancia elegida**:
  - `0.10%` relativo (`0.001`), o un límite absoluto de `50 COP` (o `0.01` USD) como mínimo, dependiendo de la moneda destino de la obligación.
- **Razón para no usar 1%**:
  - Un 1% era inaceptable como riesgo cambiario para montos grandes. Por ejemplo, en un pago de 2.000.000 COP, 1% permite 20.000 COP de diferencia, lo que escapa por mucho el simple redondeo de centavos. La tolerancia real necesaria matemáticamente para absorber centavos siempre se acota por debajo del 0.10%, garantizando seguridad sin falsos positivos.
- **Comportamiento esperado si la tasa cambia**:
  - Durante la ventana de 90 segundos de validez, el backend mantiene la cotización como un acuerdo garantizado ("contrato"). El pago descuenta estrictamente el monto `quote.source_amount`. Si expira, se obliga al cliente a solicitar una nueva tasa (recalcular).
- **Deuda técnica futura**:
  - En la versión actual V1.7, las tasas se pueden re-consultar o almacenar en memoria local de la única instancia de la API. Con un enfoque Multi-Instancia en el futuro, se requerirá un caché compartido en **Redis** o una tabla `fx_rates` temporal para optimizar llamadas a la DolarAPI.
