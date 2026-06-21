# Backend V1.2 Sprint 5 — Credit Card Cycle Semantics Report

## 1. Executive Summary
El Sprint 5 se completó exitosamente. Tras una auditoría exhaustiva de la lógica y modelo de tarjetas de crédito (`CreditCard`, `CreditCardTransaction`, `CreditCardInstallment`), se constató que la implementación base del backend para compras, pagos, separación de deuda (facturada vs. no facturada) y estimaciones de pagos en múltiples cuotas ya se alinea completamente con los lineamientos MVP para la semántica de tarjetas de crédito. Se añadieron pruebas unitarias para certificar el cumplimiento estricto del contrato financiero para el Frontend y estabilizar el comportamiento.

## 2. Problem Fixed
Previo a esta validación final, persistía la duda de si las compras se deducían inmediatamente del balance líquido (efectivo), y si existía un cálculo claro de las cuotas de tarjetas. Este sprint ha certificado, mediante revisión de código y pruebas, que las compras de tarjetas no disminuyen las cuentas *cash* sino que aumentan la deuda e interactúan correctamente con el Ledger bajo eventos del tipo `credit_card_purchase`, previniendo distorsiones operacionales.

## 3. Credit Contract Before
- La estructura soportaba alias legados (`estimated_current_debt`, `monthly_cc_payment`) y campos formales nuevos (`billed_debt`, `unbilled_debt`).
- Faltaba certeza documental y testing explícito que validara que las barreras de ciclo (`cutoff_day`, `due_day`) aislaran el consumo.

## 4. Credit Contract After
- El contrato mantiene intactos los campos ya expuestos por `CreditCardRead` y `CreditCardStatusRead`, pero ahora cuenta con cobertura de pruebas certificando su comportamiento.
- Se certifica la correcta asignación a `"credit_card_consumption_current_period"` (Snapshots) sin duplicidad como `"expense"`.
- Los campos no soportados en V1.2 quedan identificados (ej. `statement_balance = null`).

## 5. Purchase Semantics
- Creación segura a través de `create_purchase`.
- Reduce `available_credit`, aumenta `current_debt`.
- Evento del tipo `credit_card_purchase` de dirección neutral.
- Distribuye el monto total en `CreditCardInstallment`.

## 6. Payment Semantics
- Reduce `account.balance`.
- Reduce `current_debt` en la tarjeta.
- Impide sobrepagos (valida que el pago no exceda `estimated_debt`).
- Evento del tipo `credit_card_payment` de salida de fondos.

## 7. Billed / Unbilled Debt
- Se usa la vista basada en queries a `credit_card_transactions` filtrando las fechas de consumo respecto al `cycle_end_date`.
- Lo consumido hasta el ciclo anterior es deuda facturada (`billed_debt`).
- Lo consumido en el ciclo actual es deuda por facturar (`unbilled_debt`).

## 8. Cutoff / Due Day Cycle
- Estabilizado mediante `app.credit.utils.calculate_credit_card_dates`.
- Funciona correctamente cruzando fin de mes o año considerando el `cutoff_day` y `due_day`.

## 9. Installments MVP
- Los consumos generan registros asincrónicos de tipo `CreditCardInstallment` (`status="pending"` o `"paid"`).
- `next_payment_estimate` agrupa y calcula la suma del capital principal pendiente en cuotas requeridas para el mes (`YYYY-MM`).

## 10. Interest Fields
- Identificados como *stored_only*. Campos como `monthly_interest_rate`, `annual_interest_rate` existen en DB pero no aplican interés compuesto al capital facturado en esta versión (MVP de tarjeta de crédito, sin motor de intereses complejos).

## 11. Currency Handling
- Se procesan transacciones y deudas en la moneda designada a la tarjeta (`currency`).
- El cálculo total no cruza transacciones de tarjetas en divisas distintas gracias al Snapshot multi-moneda incorporado en Sprint 2.

## 12. Data Quality Warnings
- El servicio expone dinámicamente un objeto dict `data_quality` con descripciones transparentes (ej. `"payment_required": "billed_debt"` o `"statement_balance": "not_available"`).

## 13. Backward Compatibility
- Alias legacy `estimated_current_debt`, `monthly_cc_payment` han sido expuestos con validadores de retrocompatibilidad usando Pydantic. No rompe Front-end V1.1.

## 14. Tests
- Se incorporaron validaciones formales (`test_credit_semantics.py`) cubriendo ciclos, compras, aliasing. 
- Los 164 tests de la suite pasan.

## 15. OpenAPI / Docs
- `openapi.json` se encuentra al día y coherente.

## 16. Risks
- Mínimo. Las operaciones mantienen alta predictibilidad en base a consumos y periodos, delegando los cálculos pesados a SQL o funciones en Python testeables.

## 17. Final Status
El Sprint 5 se declara completado exitosamente y listo para revisión.
