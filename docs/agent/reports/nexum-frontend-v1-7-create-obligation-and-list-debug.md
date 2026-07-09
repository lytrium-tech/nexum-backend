# Nexum Frontend V1.7 - Create Obligation Runtime Debug

## Root Cause
El endpoint POST /api/v1.7/obligations fallaba con un error 500 interno (ForeignKeyViolationError) en la base de datos de PostgreSQL. La causa raíz fue un error arquitectónico en el diseño del outer_v17.py: todos los endpoints V1.7 inyectaban identity: AuthenticatedIdentity y pasaban identity.user_id (el UUID en formato string de Supabase Auth) hacia el servicio. Sin embargo, la columna user_id de la tabla obligations tiene un Foreign Key constraint que apunta hacia la llave primaria interna de la tabla transaccional users.id. Como el ID de Supabase no coincide con la llave primaria interna, la base de datos rechazaba la inserción de la obligación nueva y crasheaba la transacción. 

Adicionalmente, operaciones de lectura como list_obligations no crasheaban, pero retornaban un arreglo vacío [] silenciosamente, ya que consultaban usando el ID de Supabase que nunca arrojaba coincidencias.

## Payload Before (UI)
`json
{
  "name": "Test",
  "obligation_type": "indefinite",
  "frequency": "monthly",
  "amount_type": "fixed",
  "currency": "COP",
  "base_amount": 100000,
  "first_due_date": "2026-07-09",
  "category_id": null,
  "description": null
}
`

## Payload After
El payload de UI estaba correcto. No requirió modificaciones. Las fechas se están mandando bien.

## Error Backend Exacto
`
sqlalchemy.exc.IntegrityError: (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.ForeignKeyViolationError'>: insert or update on table "obligations" violates foreign key constraint "obligations_user_id_fkey"
DETAIL:  Key (user_id)=(c2d8eda9-8848-48a1-96bd-4c446255db25) is not present in table "users".
`

## Fix Aplicado
1. **Router V1.7:** Se modificaron los 14 endpoints en pp/obligations/router_v17.py para reemplazar identity: AuthenticatedIdentity por la dependencia transaccional current_profile: CurrentUserProfile.
2. **Router V1.7:** Se reemplazaron todas las invocaciones al servicio de identity.user_id por current_profile.id (el cual es el UUID de la tabla users validado).
3. **Service V1.7:** Se corrigieron todas las firmas de los métodos en pp/obligations/service_v17.py para que user_id sea fuertemente tipado como uuid.UUID en lugar de str.
4. **Service V1.7:** Se limpiaron todos los cast manuales uuid.UUID(user_id) que el agente anterior había dejado como parche temporal, pues ya no son necesarios.
5. **Tests:** Se actualizó el mock en 	est_obligations_v17_api.py para sobreescribir correctamente get_current_user_profile_dep en lugar de la sesión inyectada, logrando que los tests V1.7 pasen de nuevo.

## QA Runtime Real
El código backend se testeó con Pytest de nuevo (19 tests pasados para endpoints V1.7). Queda pendiente el QA Runtime por parte del usuario final al activar el entorno.
