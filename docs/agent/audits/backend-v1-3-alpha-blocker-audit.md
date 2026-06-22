# Backend V1.3 — Alpha Blocker Audit

## 1. Recommended Backend Version
**Backend V1.3 (Alpha Blocker Fixes)**. Se recomienda avanzar a esta versión para estabilizar la confianza financiera antes de considerar Alpha Ready.

## 2. Executive Summary
La auditoría confirma que los cinco blockers reportados por Frontend tienen su causa raíz en el Backend. Ninguno requiere rediseños mayores de arquitectura ni migraciones de base de datos. Los problemas derivan de comportamientos omitidos en contratos (OpenAPI), defaults estáticos y lógicas de filtrado estrictas que impiden ciclos de vida completos. 

Se propone un plan de corrección puntual que restaura la confianza financiera y alinea los contratos con las necesidades del QA.

## 3. Evidence Reviewed
- `app/obligations/schemas.py` & `service.py`: Lógica de `already_paid_this_period` y `period_status`.
- `app/accounts/router.py` & `repository.py`: Manejo de query params para `include_archived`.
- `app/ledger/schemas.py`: `LedgerEventCreate` con default `"COP"`.
- `app/intelligence/service.py` & `schemas.py`: Cálculos de `available_real` y agregación global en `SnapshotTruth`.
- `app/obligations/router.py`: Ausencia de endpoints para listar obligaciones archivadas y falta de `is_active` en `PATCH`.

## 4. Root Cause Analysis
1. **already_paid_this_period**: El backend internamente marca el periodo a saltar (`skip_periods`), pero devuelve `period_status = "paid"`. El frontend, al ver `paid_this_period = 0`, asume que hubo un error y lo rechaza mostrándolo "overdue" (o no reconoce la cobertura externa).
2. **include_archived**: En `GET /accounts?include_archived=true`, el router de FastAPI no tiene el query param correctamente anotado como `Query()` para todos los casos o el frontend envía inconsistencias que FastAPI silencia. El repositorio soporta el filtro perfectamente.
3. **Ledger Currency**: `LedgerEventCreate` define `currency="COP"` por defecto. Cuando los servicios (Cash, Credit, Obligations) originan eventos, no extraen explícitamente la moneda de la cuenta (`Account.currency`), provocando que todos los eventos se registren en COP.
4. **Snapshot Multi-Currency**: `SnapshotTruth` agrupa y suma el balance de *todas* las cuentas en variables globales (`available_real`, `free_money`, etc.) sin importar su moneda. Si hay USD y COP, los suma directamente (ej: 1,000,000 COP + 50 USD = 1,000,050).
5. **Obligations Lifecycle**: `ObligationUpdate` no permite modificar `is_active`. Además, `get_by_id` y `list` excluyen estrictamente las inoperativas. Una vez que el usuario archiva, la obligación entra en un "agujero negro" y no puede verse ni reactivarse.

## 5. already_paid_this_period
El backend debe incorporar un nuevo estado: `period_status = "covered"`.
Si el `current_period` está en `skip_periods`, debe retornar `"covered"` en lugar de `"paid"`.
Esto permitirá al frontend diferenciar un pago hecho en Nexum (paid) de uno hecho por fuera o reportado cubierto (covered).

## 6. include_archived Accounts
Se debe asegurar la correcta inyección de la variable en `list_accounts` usando `Query(False)`. Adicionalmente se verificará la serialización de FastAPI.

## 7. Ledger Currency
El campo `currency` no debe tener un fallback ciego a "COP" en `LedgerEventCreate`. La capa de servicio (cuando crea el ledger event) debe extraer explícitamente `account.currency` y pasarlo al `LedgerEventCreate`.

## 8. Snapshot Multi-Currency
Puesto que no hay conversión de divisas (No FX), la sumatoria ingenua en `SnapshotTruth` corrompe las métricas financieras globales.
**Propuesta**: Mantener la regla de "Passive multi-currency". `SnapshotTruth` (global) debe advertir severamente en `calculation_warnings` o directamente aislarse a la moneda principal del usuario, obligando al frontend a consumir `totals_by_currency`. Como corrección inmediata: los campos globales en `truth` sumarán **únicamente** el balance de las cuentas en la divisa base dominante, o bien se deprecian a favor de que el frontend pinte los valores por divisa.

## 9. Obligations Edit/Archive Lifecycle
Se debe:
1. Agregar `is_active: bool | None` a `ObligationUpdate`.
2. Añadir `include_archived: bool = Query(False)` a `GET /obligations`.
3. Quitar la restricción `is_active == True` de `get_obligation` (lectura individual) para permitir inspeccionar y reactivar obligaciones archivadas mediante `PATCH`.

## 10. Backend Fix Plan
1. **Ledger**: Extraer `currency` de la cuenta origen e insertarlo explícitamente en todo `LedgerEventCreate`.
2. **Snapshot**: Filtrar la sumatoria en `SnapshotTruth` para que no cruce divisas (solo COP por defecto) e instruir al Frontend de usar `totals_by_currency` exclusivamente.
3. **Obligations**: Implementar el estado `"covered"` e inyectar soporte total a `is_active` en esquemas y repositorios.
4. **Accounts**: Reforzar parsing de `include_archived`.

## 11. OpenAPI Contract Changes Needed
- `period_status` (string/enum) puede devolver `"covered"`.
- `ObligationUpdate` expone `is_active`.
- `GET /obligations` recibe query param `include_archived`.

## 12. Database / Migration Changes Needed
Ninguno.

## 13. Risk of Breaking Frontend
- **Bajo/Medio**: El frontend deberá soportar el string `"covered"` en `period_status`. 
- Si dependían de `SnapshotTruth` con múltiples monedas, ahora verán los valores asilados y tendrán que iterar sobre `totals_by_currency` para tener la representación real (lo cual era el objetivo de V1.2 y V1.3).

## 14. Required Tests
- Test de creación de evento en USD y validación de `currency` en Ledger.
- Test de Snapshot con COP y USD comprobando que no se sumen en `truth.available_real`.
- Test de obligaciones con `is_active=False` reactivándose mediante `PATCH`.
- Test de `period_status = "covered"`.

## 15. Implementation Order
1. Fix Ledger Event Currency.
2. Fix Snapshot Multi-Currency Sum.
3. Fix Obligations Lifecycle & `covered` status.
4. Fix `include_archived` en cuentas.
5. Regenerate OpenAPI.

## 16. Questions For Steven
- Para el Snapshot Multi-Currency, ¿es preferible que `SnapshotTruth` global devuelva 0/null y se obligue al frontend a usar solo `totals_by_currency`, o dejamos que global sume únicamente las cuentas de la "divisa dominante" (COP) ignorando silenciosamente los USD para el total global?

## 17. Go / No-Go For Implementation
**GO**. Esperando directriz sobre la pregunta en el punto 16.
