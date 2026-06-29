# Nexum V1.5 - Pay Early Summary Stale Debt Debug

## Problema

Al realizar un pago anticipado (early payment) de una cuota de tarjeta de crédito, la cuota se marca correctamente como pagada o parcialmente pagada (`paid_amount` aumenta), pero el saldo total (`current_debt`) y el desglose de deuda (`billed_debt`, `unbilled_debt`) en el resumen de la tarjeta no reflejaban la reducción. El frontend seguía mostrando la deuda original, a pesar de que la cuota ya estaba cubierta.

## Diagnóstico

El cálculo del estado y deuda de la tarjeta (`_calculate_card_status` en `CreditCardService` y `get_card_status_data` en `CreditCardRepository`) se basaba exclusivamente en sumar las transacciones base (`credit_card_transactions`) filtradas por tipo `purchase` y restando aquellas de tipo `payment`.

Sin embargo, el pago anticipado de cuotas (Sprint 5) genera un registro independiente en `credit_card_early_payments` y **no** inserta una transacción de tipo `payment` en `credit_card_transactions`. Adicionalmente, el pago temprano actualiza el `paid_amount` directamente en la tabla `credit_card_installments`. Como el cálculo de deuda dependía únicamente de la tabla de transacciones e ignoraba el estado de las cuotas, los pagos anticipados eran completamente invisibles para los endpoints de resumen de crédito (ej: `GET /api/v1/credit/cards/{card_id}/summary`).

## Solución Aplicada

Se modificaron los métodos `get_card_debt` y `get_card_status_data` en `app/credit/repository.py` para que la deuda y los saldos facturados/no facturados se calculen directamente desde el modelo avanzado de cuotas (`credit_card_installments`) en lugar de depender de las transacciones crudas.

La nueva lógica de cálculo utiliza:
```sql
SUM(principal_amount + interest_amount - paid_amount)
```

**Ventajas de la solución:**
1. **Evita mutar `principal_amount`**: Se cumple la regla estricta de mantener el `principal_amount` original sin modificar.
2. **Fuente de verdad unificada**: El estado de la deuda ahora refleja con precisión matemática el estado de todas sus cuotas subyacentes. Si una cuota tiene un `paid_amount` mayor (ya sea por un pago normal a la tarjeta o por un `early_payment` dirigido), el total de deuda facturada o no facturada disminuye proporcionalmente de forma automática.
3. **Consistencia total**: Los `226` tests pasaron con éxito, validando que el comportamiento no rompe el modelo financiero existente, y que `v_credit_card_debt` no interferirá dado que ahora los cálculos leen la tabla correcta a nivel de cuota.

## Estado de Validación
- Cambios realizados en `app/credit/repository.py`.
- Suite de pruebas completa (`python -m uv run pytest tests/ -v`): `226/226 passed`.
- **Aprobación pendiente** por parte de Runtime QA / Steven para proceder a commit y deploy.
