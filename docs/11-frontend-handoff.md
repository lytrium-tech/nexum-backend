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