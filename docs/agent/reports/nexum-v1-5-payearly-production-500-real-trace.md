# Nexum V1.5 — Pay Early 500 Real Production Trace

## Resumen del Bug en Producción
A pesar de la corrección anterior sobre la mutación de `payload.amount`, el pago anticipado completo elegible (con `amount=None` o cubriendo el 100% de la deuda) continuaba fallando en producción con un `Error 500` pero no en los tests locales con SQLite.

## Análisis del Traceback de Producción (VPS)
Revisando los logs de `docker compose logs --tail=300 api`, el error exacto reportado fue:
```text
sqlalchemy.exc.IntegrityError: (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.CheckViolationError'>: new row for relation "credit_card_installments" violates check constraint "credit_card_installments_principal_amount_check"
```
**Endpoint:** `POST /api/v1/credit/cards/{card_id}/purchases/{purchase_id}/pay_early`
**Payload:** `{"account_id": "...", "amount": null, "allocation_mode": "reduce_installment_amount"}`

## Causa Raíz
La base de datos PostgreSQL de producción cuenta con una restricción `CHECK (principal_amount > 0)` (`credit_card_installments_principal_amount_check`), definida en `migrate_v11_sprint5.py`.
Cuando un pago anticipado cubre la totalidad del principal restante (`remaining_to_allocate = 0.00`), la lógica de recálculo (`_installment_amounts`) asigna `0.00` a las cuotas futuras no facturadas.
Al intentar actualizar la cuota en DB con `principal_amount=0.00`, Postgres (a diferencia de SQLite) rechaza el UPDATE lanzando el `IntegrityError` y colapsando el request.

## Solución Aplicada
En lugar de forzar un `principal_amount` inválido, se modificó la lógica en `app/credit/service.py` para que, cuando el nuevo principal deba ser cero (`new_principal <= 0.00`):
1. No se modifica el `principal_amount` original (manteniendo contenta a la base de datos).
2. Se asigna `inst.paid_amount = inst.principal_amount` para reflejar un pago completo de esa cuota particular.
3. Se marca `inst.status = "paid"`.

De esta forma, la matemática del saldo deudor remanente es correcta (`principal - paid = 0`) y la cuota queda propiamente cancelada anticipadamente sin violar invariantes estructurales de la DB.

## Validaciones
- Se corrigió el caso de prueba `test_early_payment_amount_none_succeeds` en `tests/unit/test_multicurrency_qa.py` para verificar que `paid_amount == principal_amount` en lugar de `principal_amount == 0.00`.
- El suite completo pasó (226/226).
- `ruff check .` validó el formateo sin incidentes.
