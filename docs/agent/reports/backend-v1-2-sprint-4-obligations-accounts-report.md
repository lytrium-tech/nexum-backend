# Backend V1.2 Sprint 4 — Obligations Lifecycle & Account Archiving Report

## 1. Executive Summary
El Sprint 4 se completó con éxito. Se implementaron las correcciones funcionales críticas solicitadas sobre el ciclo de vida de las obligaciones y el archivado seguro de cuentas. El backend ahora asume la responsabilidad total de calcular estados periódicos ("pending", "paid", "partial", "overdue") y previene doble deducción. Asimismo, se incorporó un mecanismo seguro para archivar cuentas (soft delete) y evitar eliminaciones físicas de cuentas con historial financiero.

## 2. Problem Fixed
Previo al Sprint 4, el estado periódico de las obligaciones no manejaba correctamente la reactivación mensual ni los casos de creación tardía en el mes. Además, la eliminación de cuentas se limitaba a un soft delete básico sin un comportamiento de exclusión en los endpoints de listado, lo cual podía generar inconsistencias en la presentación del frontend o permitir hard deletes inseguros.

## 3. Obligations Contract Before
- `period_status` no excluía los periodos previamente pagados explícitamente en la creación.
- No había soporte en `ObligationCreate` para indicar que el pago del primer periodo ya fue cubierto fuera del sistema.

## 4. Obligations Contract After
- Se agregaron las banderas `already_paid_this_period`, `start_next_period` y `pending_this_period` a `ObligationCreate`.
- Si alguna bandera indica omitir este periodo, el backend almacena el periodo actual en `metadata["skip_periods"]`.
- `remaining_amount` devuelve `0.00` si el periodo actual está excluido.
- `period_status` devuelve `"paid"` si el periodo actual está excluido.

## 5. Period Lifecycle Semantics
- Las obligaciones mensuales se reactivan en un nuevo periodo automáticamente debido a que `paid_this_period` se calcula dinámicamente según el periodo (ej. `2026-06`, `2026-07`).
- Los estados cubren `"inactive"`, `"pending"`, `"paid"`, `"partial"`, y `"overdue"`.

## 6. Overdue Semantics
- Si `status != "paid"` y existe un `due_day`, se verifica si la fecha actual es mayor a la fecha de vencimiento (`due_date` del periodo en curso). Si es así, se retorna `"overdue"`.
- Una obligación completamente pagada no se marca como vencida.

## 7. Creation With Past Due Day
- Si el usuario crea una obligación en una fecha posterior a `due_day`, y utiliza el flag `already_paid_this_period` o `start_next_period`, el periodo se ignora para cobro (`status="paid"`, `remaining_amount=0`), impidiendo así doble deducción y previniendo un estado de mora irreal.

## 8. Double Deduction Protection
- El backend rechaza un segundo pago para `fixed_full_payment` en el mismo periodo (`ObligationAlreadyPaidError`).
- Se validan de forma estricta los modos `partial_allowed`.
- El endpoint de pago sigue admitiendo claves de idempotencia para prevenir duplicaciones de red.

## 9. Account Archiving Contract Before
- El endpoint de listado (`GET /accounts`) incluía todas las cuentas, sin filtrar las desactivadas, o lo hacía pero sin permitir una opción explícita.
- La eliminación simplemente marcaba `is_active=False` de manera silenciosa, sin intentar un borrado físico para cuentas vacías sin uso.

## 10. Account Archiving Contract After
- El endpoint `GET /accounts` ahora acepta el parámetro `include_archived=False` (por defecto), ocultando las cuentas inactivas.
- El usuario puede recuperar el historial si envía `include_archived=True`.

## 11. Hard Delete Safety
- La acción de borrar cuenta (`DELETE /accounts/{id}`) ahora intenta realizar un borrado físico seguro (hard delete).
- Si la cuenta tiene referencias (Eventos Financieros, etc.), la base de datos lanza un `IntegrityError`. El backend lo atrapa de forma elegante y rechaza la eliminación recomendando el archivado.

## 12. Historical Integrity
- Todas las cuentas con transacciones pueden archivarse.
- Los eventos financieros preservan su relación foránea porque el archivo es lógico, por lo cual los reportes históricos del Ledger continúan funcionando perfectamente.

## 13. Backward Compatibility
- Los endpoints mantienen compatibilidad estructural.
- Se ha expuesto la bandera de archivo como opcional.
- La estructura de pago actual permanece idéntica en el cuerpo base.

## 14. Tests
- Se añadieron tests en `tests/unit/test_accounts.py` para simular fallas de integridad durante un borrado y asegurar la protección de historia.
- Se añadieron tests en `tests/unit/test_obligations_semantics.py` para asegurar que las obligaciones operan según las nuevas semánticas periódicas, incluyendo `already_paid_this_period`.
- Los 158 tests pasan correctamente.

## 15. OpenAPI / Docs
- `openapi.json` se ha regenerado exitosamente.

## 16. Risks
- Mínimo. Las cuentas archivadas ya no aparecerán en las selecciones predeterminadas de cuenta del frontend, resolviendo un viejo bug.

## 17. Final Status
El Sprint 4 se declara completado exitosamente y listo para revisión.
