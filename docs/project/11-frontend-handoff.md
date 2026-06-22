# Frontend Integration Handoff

## Backend Base URL
- **Producción**: https://api.nexum.lytrium.tech
- **Desarrollo**: http://localhost:8000

## Autenticación
- Header: Authorization: Bearer <Access Token de Supabase>
- Endpoint de Onboarding: POST /api/v1/users/me/bootstrap (Obligatorio tras el registro inicial). Idempotente.

## Onboarding Flow
1. Login/Signup en Supabase.
2. Llamar a /bootstrap.
3. (Recomendado) Forzar la creación de una cuenta/billetera inicial.

## Endpoints Clave
- GET /api/v1/users/me
- GET /api/v1/intelligence/snapshot
- POST /api/v1/conversations/message

## Errores Comunes
- 401 Unauthorized: Token faltante, expirado o firma inválida.
- 404 Not Found en finanzas: El usuario existe en Supabase pero no ha hecho /bootstrap.

## Backend V1.4 Multi-Currency & FX
A partir de V1.4:
1. **Todo POST financiero exige `currency`**. No hay defaults a `COP`.
2. **SnapshotTruth y Multi-Moneda**: Si el usuario tiene cuentas en varias monedas, `SnapshotTruth` zeroea los totales globales y dispara un warning `cross_currency_global_totals_disabled`.
3. **totals_by_currency**: Única fuente financiera válida. Frontend debe mapear las tarjetas según el índice de esta colección.
4. **estimated_totals**: Para mostrar un Net Worth unificado, frontend DEBE usar el objeto `estimated_totals` de `GET /api/v1/intelligence/snapshot`. Si `estimated_totals.unsupported_currencies` tiene elementos, el frontend debe mostrar un icono de alerta indicando que esa moneda específica no se pudo sumar al total estimado.