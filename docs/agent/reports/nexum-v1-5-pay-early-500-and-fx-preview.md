# Nexum V1.5 — Pay Early 500 & FX Preview Fix

## Resumen

Se ha completado la corrección del "blocker" restante reportado en QA relacionado al error 500 en pago anticipado, y se han expuesto endpoints de preview en el backend para cálculo de FX según requerimientos.

## Diagnóstico del Bug 1 (Pay early elegible sigue dando 500)
El origen del error 500 era un fallo de mutación en la instancia local de Pydantic (`payload.amount`) dentro de `app/credit/service.py`. Durante el cálculo del "full payment" (`amount=None`), el backend asignaba un nuevo valor al atributo `amount`. Al crear el `LedgerEventCreate` o `CreditCardEarlyPayment`, esta mutación generaba fallos de validación o inconsistencias numéricas si el monto resultaba diferente a las reglas estrictas de persistencia, arrojando excepciones no capturadas. Adicionalmente, el pago de tarjetas sin deuda principal pendiente causaba excepciones extrañas. 

**Solución aplicada:**
- Modificada la lógica de asignación para que use una variable inmutable en la función (`source_amount`) dejando `payload.amount` intacto.
- Añadida explícitamente la validación `total_remaining_principal <= 0`, que ahora arroja un controlable `CreditDomainError` con `PAY_EARLY_NOT_ELIGIBLE: Remaining principal is zero.` en lugar de provocar un colapso en validaciones de Ledger posteriores.

## Diseño UX Seguro de Multi-moneda (Bug 2)
Tal como se estipuló en los lineamientos ("Backend calcula, Frontend representa"), el frontend no debe intentar buscar divisas ni hacer cálculos, especialmente en contextos cruzados donde la obligación o la compra es USD y la cuenta es COP. 

Se introdujeron dos endpoints robustos de **Preview**, que reciben `account_id` y opcionalmente `amount` para ejecutar todo el flujo `get_fx_rate()` sin persistir.

### Nuevos Endpoints:
- `POST /api/v1/obligations/{obligation_id}/payments/preview`
- `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early/preview`

**Payload (Ambos endpoints):**
```json
{
  "account_id": "uuid",
  "amount": null
}
```
*(Se soporta `amount` para pagos parciales de obligaciones variables, mientras que para obligaciones fijas o pago anticipado suele pasarse nulo o exacto).*

**Response:**
```json
{
  "source_amount": "150000.00",
  "source_currency": "COP",
  "target_amount": "37.50",
  "target_currency": "USD",
  "fx_rate": "4000.00",
  "rate_source": "dolarapi_colombia",
  "is_estimated": true
}
```

## Pruebas Adicionales
- Se reparó similar lógica de mutación propensa a errores que existía en `app/obligations/service.py` (`payload.amount` modificado dinámicamente) protegiéndola bajo la variable `source_amount`.
- La suite completa de tests de regresión multimoneda (`tests/unit/test_multicurrency_qa.py`) ejecutada verificó `25/25` éxitos.
- `ruff check .` finalizó exitosamente.
- `openapi.json` se regeneró y actualizó para uso del Frontend.

**Nota para el siguiente agente:** Backend está listo para ser revisado o desplegado.
