# Backend V1.6.1 — Intelligence Snapshot Hotfix

## 1. Executive Summary
El hotfix resuelve un error `HTTP 500` en el endpoint `GET /api/v1/intelligence/snapshot` causado por incompatibilidad de la agregación financiera con el modelo Obligations V1.6. Se migró exitosamente la lógica del servicio de inteligencia para que lea `ObligationPeriod` en lugar del modelo legacy `Obligation`, restaurando el funcionamiento del dashboard.

## 2. Root Cause
`IntelligenceService.get_snapshot` fallaba al intentar acceder a propiedades y métodos eliminados en Obligations V1.6 (`paid_this_period`, `period_status`, `remaining_amount`, `get_period_payments`).

## 3. Legacy Dependencies Removed
Se eliminó por completo el uso de:
- `ObligationRepository.get_period_payments`
- Propiedades no declaradas de `ObligationRead` (`period_status`, `remaining_amount`, `paid_this_period`).
- La iteración manual de templates `Obligation` en `get_snapshot`.

## 4. V1.6 Period-Based Logic
Se implementó `ObligationRepository.get_pending_period_amounts_for_snapshot(user_id)`.
- **Estados incluidos en deuda:** `overdue`, `pending_payment`, `partially_paid`.
- **Regla contable:** Suma de `(amount - paid_amount)` para cada periodo (asumiendo 0 si `paid_amount` es null).
- **Protección Null:** Los periodos donde `amount` es null (`pending_amount_definition`) se excluyen expresamente de la suma global para evitar sumar `None`.

## 5. Accounting Decision for pending_amount_definition
Tal como fue solicitado, los periodos con el estado `pending_amount_definition` han sido excluidos de la agregación `committed_outflows` porque no poseen un monto base real y no deben inventar deuda financiera en el snapshot. Sólo se incluyen cuando el monto es definido.

## 6. Multicurrency Behavior
La agregación respeta la separación multidivisa implementada en V1.6. `get_pending_period_amounts_for_snapshot` agrupa la deuda por moneda de la obligación base (`GROUP BY Obligation.currency`), alimentando individualmente cada `CurrencyMetrics` en `get_snapshot`. El bloqueo superior para cruce indebido de divisas se mantuvo intacto.

## 7. Tests Added/Updated
Se refactorizó completamente `tests/unit/test_intelligence.py` para erradicar los mocks legacy (`list_by_user`, `get_period_payments`). 
- El caso ignorado (`test_committed_outflows_excludes_paid_obligations`) se restauró y simplificó para comprobar únicamente las salidas requeridas.
- Se introdujo `test_get_pending_period_amounts_for_snapshot` que simula un query `session.execute` para verificar las sumas matemáticas y el filtrado por divisas sobre objetos V1.6, asegurando tolerancia a nulos.

## 8. Validation Results
- Pruebas superadas: 9/9
- Linter (`ruff check`): Pasado.
- Formateo de código: Restringido a los 3 archivos tocados.
- Tests específicos (`pytest -k snapshot or intelligence`): Pasado.

## 9. Deployment Recommendation
El hotfix es seguro y puede ser desplegado a producción (`https://api.nexum.lytrium.tech`). Se aconseja un `git commit` inmediato y `git push`.

## 10. Risks / Rollback Notes
Riesgo **Nulo**. Sólo se impactaron endpoints read-only. Rollback consiste en revertir el commit del hotfix, pero devolvería a la API al estado de 500.
