# Nexum V1.5 — Fixed Obligation FX + Credit Pay Early Eligibility Debug Report

## 1. Executive Summary
Este reporte documenta los diagnósticos y correcciones aplicadas a los tres bugs restantes de Runtime QA V1.5 del backend. Se resolvieron de forma robusta las validaciones de pagos multimoneda para obligaciones fijas, la seguridad de tipos al pagar cuotas anticipadamente sin especificar monto (`amount=None`), y las restricciones de elegibilidad de pagos anticipados para compras de 1 cuota o sin cuotas futuras.

---

## 2. Diagnóstico y Correcciones Detalladas

### Bug 1: Obligaciones de Monto Fijo Multimoneda
* **Síntoma:** Al pagar una obligación de monto fijo en COP usando una cuenta USD, si el monto ingresado superaba el saldo de la cuenta pero no el requerido, o si el monto convertido era menor al requerido, se arrojaba un error confuso de "Saldo insuficiente en la cuenta..." en lugar de una validación limpia de desajuste de montos.
* **Causa Raíz:** En `create_payment` (dentro de `app/obligations/service.py`), la validación del saldo de la cuenta (`account.balance < payload.amount`) se realizaba al inicio del método, antes de validar el monto contra las reglas de negocio de `fixed_full_payment`. Al evaluar monedas cruzadas, esto causaba comparaciones no válidas o arrojaba error de saldo insuficiente prematuro.
* **Corrección:** Se desplazó la validación de fondos en la cuenta origen al final de la fase de validación (después de comprobar los modos de pago de la obligación). De esta manera:
  1. Si el pago convertido no cubre el monto fijo de la obligación, se lanza de forma prioritaria `ObligationAmountMismatchError` (HTTP 409).
  2. Si el pago sí cubre la cuota fija, se evalúa si los fondos de la cuenta origen en su propia moneda cubren el pago solicitado, lanzando `InsufficientFundsError` en caso contrario.

### Bug 2: Crash (500) en Pago Anticipado de Tarjeta (`pay_early`)
* **Síntoma:** Al llamar al endpoint `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early`, si el usuario no especificaba un monto en el payload (dejando `amount=None`), el backend arrojaba un HTTP 500 debido a un crash de tipos:
  `TypeError: '<' not supported between instances of 'decimal.Decimal' and 'NoneType'`
* **Causa Raíz:** En `create_early_payment` (dentro de `app/credit/service.py`), se comprobaba `account.balance < payload.amount` sin considerar que `payload.amount` de `CreditCardEarlyPaymentCreate` puede ser `None` (indicando pago total anticipado).
* **Corrección:** Se reordenó la carga de datos para resolver la compra y cuotas primero. Si `payload.amount` es `None`, se calcula automáticamente la suma del principal pendiente (`total_remaining_principal`) y se convierte a la moneda de la cuenta origen (usando el tipo de cambio FX), asignándolo de vuelta a `payload.amount` antes de validar el saldo de la cuenta y registrar el Ledger event.

### Bug 3: Restricción de Elegibilidad para Pago Anticipado (`pay_early`)
* **Síntoma:** Compras de una sola cuota o compras que ya están completamente facturadas/pagadas aparecían como elegibles en el sistema.
* **Causa Raíz:** El motor del backend no validaba activamente si la compra de origen constaba de más de 1 cuota, ni si existían cuotas futuras no facturadas (`unbilled/pending`) antes de procesar el pago anticipado.
* **Corrección:** 
  1. Se añadieron dos validaciones controladas en `create_early_payment`:
     - Si la compra posee un total de 1 cuota en su origen, se rechaza la operación lanzando `CreditDomainError` con el código `"PAY_EARLY_NOT_ELIGIBLE: Single-installment purchases are not eligible for early payment."`.
     - Si la compra no tiene cuotas futuras pendientes (es decir, cuotas con `scheduled_period` superior al periodo actual), se rechaza con `"PAY_EARLY_NOT_ELIGIBLE: No future pending/unbilled installments found for early payment."`.
  2. Se expuso un nuevo campo dinámico `@computed_field` en `CreditCardInstallmentRead` (dentro de `app/credit/schemas.py`) llamado `is_pay_early_eligible: bool`, el cual determina en tiempo de ejecución si una cuota particular es apta para pago anticipado (si es de una compra de más de 1 cuota, no está pagada, y corresponde a un periodo futuro).
  3. Se regeneró el contrato OpenAPI mediante `export_openapi.py` reflejando este nuevo campo en la especificación de `CreditCardInstallmentRead`.

---

## 3. Cobertura de Pruebas Unitarias

Se actualizaron y agregaron test cases específicos en [test_multicurrency_qa.py](file:///C:/Users/Lytrium/Documents/Projects/Nexum/backend/tests/unit/test_multicurrency_qa.py):
* `test_fixed_obligation_cop_paid_from_usd_enough_succeeds`: Pago de obligación COP fija con saldo USD suficiente.
* `test_fixed_obligation_usd_paid_from_cop_enough_succeeds`: Pago de obligación USD fija con saldo COP suficiente.
* `test_fixed_obligation_insufficient_source_balance_validates_in_source`: Cuenta USD con saldo insuficiente para pagar obligación COP fija falla en moneda origen (USD).
* `test_fixed_obligation_amount_mismatch_error_not_insufficient_funds`: Pago con monto menor a la cuota fija retorna mismatch error en vez de saldo insuficiente.
* `test_early_payment_amount_none_succeeds`: Envío de `amount=None` calcula automáticamente la amortización total requerida.
* `test_early_payment_single_installment_rejected`: Compra de 1 cuota lanza error controlado de no elegibilidad.
* `test_early_payment_no_future_installments_rejected`: Compra sin cuotas futuras pendientes lanza error controlado.

---

## 4. Estado de Validación Local

* **Unit Tests:** **222/222 PASSED** (100% de la suite de backend).
* **Ruff Check & Format:** **Clean (passed)**.
* **OpenAPI Contract tests:** **Passed**.
